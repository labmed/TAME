"""Full-column semantic review and explicit, replayable init decisions.

No numeric sex code is assigned a meaning from its frequency or spelling.
The same core options are usable from the CLI and from a definitions TOML.
"""
from collections import Counter
from copy import deepcopy
import math
import re

from .age import parse_age, age_band_label
from .categories import category_vocabularies, _vocab_for_column, _row_synonyms
from .cellstate import STATE_VALUE, NULL, cell_state, state_counts
from .models import ColumnSpec
from .sex import normalize_sex, SEX_CANONICAL_VALUES


def source_columns(dataset):
    return [c.name for c in dataset.columns if c.has_tag("SOURCE") or
            c.name.lower() in {"source", "source_dataset", "source_id", "dataset"}]


def sex_targets(dataset, column):
    """Resolve every row by its own source, with declared maps taking precedence."""
    vocabs = category_vocabularies(dataset.meta)
    key = _vocab_for_column(dataset, column, vocabs)
    if key:
        raw_spec = dataset.meta.get("CATEGORIES", {}).get(key, {})
        from .config import ci_get
        maps = [ci_get(raw_spec, "MAP", {})] + list(ci_get(raw_spec, "SOURCE_MAPS", {}).values())
        for mapping in maps:
            seen = {}
            for raw, target in mapping.items():
                token = str(raw).strip().lower()
                meaning = normalize_sex(target) or str(target)
                if token in seen and seen[token] != meaning:
                    raise ValueError(f"Conflicting sex mappings after case/whitespace normalization: {raw!r}")
                seen[token] = meaning
    result = []
    for pos, (idx, value) in enumerate(dataset.df[column.name].items()):
        target = None
        if cell_state(value) == STATE_VALUE:
            mapping = _row_synonyms(dataset, vocabs[key], idx, pos) if key else {}
            raw = str(value).strip().lower()
            # An invalid declared target must not be masked by the built-in aliases.
            target = normalize_sex(mapping[raw]) if raw in mapping else normalize_sex(value)
        result.append(target)
    return result


def age_input(value, unit="a"):
    """Apply an explicitly selected unit only to bare numeric ages."""
    if cell_state(value) == STATE_VALUE and unit != "a" and re.fullmatch(r"\s*[+]?(?:\d+(?:\.\d*)?|\.\d+)\s*", str(value)):
        return str(value).strip() + unit
    return value


def semantic_profiles(dataset):
    """All rows contribute; only issue row-number examples are bounded."""
    profiles = []
    candidates = source_columns(dataset)
    options = dataset.meta.get("INIT_OPTIONS", {})
    for col in dataset.columns:
        if not col.has_any_tag(("SEX", "AGE")):
            continue
        series = dataset.df[col.name]
        profile = {"column": col.name, "semantic": "SEX" if col.has_tag("SEX") else "AGE",
                   "rows_scanned": len(series), "states": state_counts(series),
                   "recognized_cells": 0, "unrecognized_cells": 0, "changed_if_applied": 0}
        counts, bad_rows = Counter(), []
        if col.has_tag("SEX"):
            targets = sex_targets(dataset, col)
            key = _vocab_for_column(dataset, col, category_vocabularies(dataset.meta))
            declared_source = category_vocabularies(dataset.meta)[key]["source_column"] if key else None
            source = declared_source or (candidates[0] if len(candidates) == 1 else None)
            if source and source not in dataset.df.columns:
                raise ValueError(f"Category source column is absent: {source}")
            profile.update(source_column=source or "", source_candidates=candidates)
            for pos, value in enumerate(series):
                if cell_state(value) != STATE_VALUE:
                    continue
                src = dataset.df[source].iloc[pos] if source else None
                src = str(src) if cell_state(src) == STATE_VALUE else ""
                counts[(src, str(value), targets[pos] or "")] += 1
            profile["values"] = [{"source": s, "raw_value": v, "canonical_value": t,
                                   "count": n, "recognized": bool(t)} for (s, v, t), n in counts.items()]
        else:
            unit = options.get("AGE", {}).get(col.name, {}).get("UNIT", "a")
            if unit not in {"a", "mo", "d"}:
                raise ValueError(f"INIT_OPTIONS.AGE.{col.name}.UNIT must be a, mo or d")
            profile.update(bare_numeric_unit=unit, units={}, minimum_years=None, maximum_years=None)
            years, units = [], Counter()
            for pos, value in enumerate(series):
                if cell_state(value) != STATE_VALUE:
                    continue
                parsed = parse_age(age_input(value, unit))
                counts[(str(value), parsed.canonical_value if parsed else "")] += 1
                if parsed:
                    years.append(parsed.years)
                    units[parsed.unit_code] += 1
                elif len(bad_rows) < 10:
                    bad_rows.append(pos + 2)
            profile["values"] = [{"raw_value": v, "canonical_value": t, "count": n,
                                   "recognized": bool(t)} for (v, t), n in counts.items()]
            profile.update(units=dict(units), invalid_row_examples=bad_rows,
                           minimum_years=min(years) if years else None, maximum_years=max(years) if years else None)
        for item in profile["values"]:
            profile["recognized_cells" if item["recognized"] else "unrecognized_cells"] += item["count"]
            if item["recognized"] and item["raw_value"] != item["canonical_value"]:
                profile["changed_if_applied"] += item["count"]
        profiles.append(profile)
    return profiles


def _choice(prompt, choices, default, read, emit):
    while True:
        try:
            answer = read(prompt).strip().lower() or default
        except EOFError as exc:
            raise ValueError("Interactive init interrupted; no output was written. Use --no-interactive for batch work.") from exc
        if answer in choices:
            return answer
        emit("선택 가능한 값: " + ", ".join(choices))


def _replace_tags(dataset, name, tags):
    dataset.columns = [ColumnSpec(c.original_header, c.name, tags) if c.name == name else c for c in dataset.columns]
    dataset.meta.setdefault("COLUMN", {}).setdefault(name, {})["TAGS"] = list(tags)


def configure_interactively(dataset, *, read=None, emit=None):
    """Gather decisions before writing; a declined or unmapped value stays intact."""
    reader, emit = read or input, emit or print
    def read(prompt):
        try:
            return reader(prompt)
        except EOFError as exc:
            raise ValueError("Interactive init interrupted; no output was written. Use --no-interactive for batch work.") from exc
    ds = dataset.replace(df=dataset.df.copy(), columns=list(dataset.columns), meta=deepcopy(dataset.meta))
    opts = ds.meta.setdefault("INIT_OPTIONS", {})
    initial = semantic_profiles(ds)
    emit(f"전체 {len(ds.df):,}행을 검사했습니다. 숫자 성별 코드는 코드북에 따라 직접 정의해야 합니다.")
    for p in initial:
        name = p["column"]
        emit(f"\n[{name} → {p['semantic']}] 값 {p['states']['VALUE']:,}개, 인식 {p['recognized_cells']:,}, 미인식 {p['unrecognized_cells']:,}, 비값 상태 {len(ds.df) - p['states']['VALUE']:,}개")
        if p["semantic"] == "SEX":
            for item in p["values"]:
                emit(f"  {item['source'] + ': ' if item['source'] else ''}{item['raw_value']!r} × {item['count']:,} → {item['canonical_value'] or '매핑 필요'}")
            if _choice("성별(SEX) 열이 맞습니까? [Y/n]: ", ("y", "n"), "y", read, emit) == "n":
                _replace_tags(ds, name, ("STR",))
                opts.setdefault("SEX", {}).pop(name, None)
                continue
            normalize = _choice("지금 정규화하고 원값을 별도 보존할까요? [y/N]: ", ("y", "n"), "n", read, emit) == "y"
            opts.setdefault("SEX", {})[name] = {"NORMALIZE": normalize}
            if not normalize:
                continue
            source = p["source_column"]
            if not source and p["unrecognized_cells"]:
                emit("출처별 코드가 다르면 그 출처를 구별하는 열을 지정하세요. 출처 정보 없이는 서로 다른 코드 체계를 구별할 수 없습니다.")
                while True:
                    source = read("매핑의 출처 열 이름 [Enter: 공통 매핑]: ").strip()
                    if not source or source in ds.df.columns and source != name:
                        break
                    emit("입력 자료에 있는 다른 열 이름을 입력하세요.")
            # Expand every observed spelling into a standard category vocabulary so
            # later normalize-categories can reproduce the same decisions.
            vocabs = ds.meta.setdefault("CATEGORIES", {})
            key = "INIT_SEX_" + str(next(i for i in range(1, len(vocabs) + 2) if "INIT_SEX_" + str(i) not in vocabs))
            entry = {"VALUES": list(SEX_CANONICAL_VALUES), "STRICT": True}
            col = next(c for c in ds.columns if c.name == name)
            targets = sex_targets(ds, col)
            observed = {}
            for pos, value in enumerate(ds.df[name]):
                if cell_state(value) != STATE_VALUE:
                    continue
                src = ds.df[source].iloc[pos] if source else None
                src = str(src) if cell_state(src) == STATE_VALUE else ""
                observed.setdefault((src, str(value)), targets[pos])
            mappings = {}
            for (src, value), target in observed.items():
                if not target:
                    target = _choice(f"  {name} / {src or '공통 또는 출처 없음'} / {value!r}: male/female/other/unknown/skip [skip]: ",
                                     (*SEX_CANONICAL_VALUES, "skip"), "skip", read, emit)
                if target != "skip" and (not source or src):
                    mappings.setdefault(src, {})[value] = target
            if source:
                entry.update(SOURCE_COLUMN=source, SOURCE_MAPS=mappings or {"": {}})
                if any(not src for src, _ in observed):
                    emit("출처가 비어 있는 행의 미확인 코드는 유지하며 검토 항목으로 남깁니다.")
            else:
                entry["MAP"] = mappings.get("", {})
            vocabs[key] = entry
            _replace_tags(ds, name, tuple(dict.fromkeys((*col.tags, "CATEGORY", key))))
            # Remove previous vocab references: the reviewed vocabulary is authoritative.
            old_keys = set(vocabs) - {key}
            _replace_tags(ds, name, tuple(t for t in next(c for c in ds.columns if c.name == name).tags if t not in old_keys))
        else:
            emit("단위 없는 값은 기본적으로 년(a), mo는 월, d는 일입니다. 연령의 형식 검사는 임상적 타당성의 확정이 아닙니다.")
            if _choice("개별 연령(AGE) 열이 맞습니까? [Y/n]: ", ("y", "n"), "y", read, emit) == "n":
                _replace_tags(ds, name, ("STR",))
                opts.setdefault("AGE", {}).pop(name, None)
                opts.setdefault("AGE_INTEGER", {}).pop(name, None)
                opts.setdefault("AGE_GROUPS", {}).pop(name, None)
                continue
            unit = _choice("단위 없는 값의 실제 단위 a(년)/mo(월)/d(일) [a]: ", ("a", "mo", "d"),
                           opts.get("AGE", {}).get(name, {}).get("UNIT", "a"), read, emit)
            opts.setdefault("AGE", {})[name] = {"UNIT": unit}
            p = next(v for v in semantic_profiles(ds) if v["column"] == name)
            emit(f"  연 단위 범위: {p['minimum_years']}–{p['maximum_years']}; 파싱 실패 {p['unrecognized_cells']:,}개")
            for item in p["values"]:
                if not item["recognized"]:
                    emit(f"  연령으로 해석 불가: {item['raw_value']!r} × {item['count']:,}")
            age_count = sum(v["semantic"] == "AGE" for v in initial)
            observed_units = {key: value for key, value in p["units"].items() if value}
            mixed_units = len(observed_units) > 1
            if mixed_units:
                unit_text = ", ".join(f"{key} {value:,}개" for key, value in observed_units.items())
                emit(f"연령 단위가 섞여 있습니다: {unit_text}. 원본 AGE 열은 보존됩니다.")
                existing_integer = opts.get("AGE_INTEGER", {}).get(name)
                prompt = "완료 연수(소수점 이하 버림) 정수 열을 추가할까요? [Y/n]: " if existing_integer else "완료 연수(소수점 이하 버림) 정수 열을 추가할까요? [y/N]: "
                create_integer = _choice(prompt, ("y", "n"), "y" if existing_integer else "n", read, emit) == "y"
                opts.setdefault("AGE_INTEGER", {}).pop(name, None)
                if create_integer:
                    invalid = "ERROR"
                    if p["unrecognized_cells"]:
                        if _choice("해석 불가 값은 정수 연령 열에서 NULL로 남길까요? 원 연령은 보존합니다. [y/N]: ",
                                   ("y", "n"), "n", read, emit) != "y":
                            create_integer = False
                        else:
                            invalid = "NULL"
                    if create_integer:
                        output = "AGE_INTEGER" if age_count == 1 else name + "_INTEGER"
                        opts["AGE_INTEGER"][name] = {"OUTPUT": output, "METHOD": "FLOOR", "INVALID_POLICY": invalid}
                        emit(f"  생성할 완료 연수 열: {output}")
            emit("연령군: <1, 1–4(5년군) 또는 1–9(10년군), 이후 5/10년 간격, 70+. 소수 연령은 해당 반개구간에 포함합니다.")
            choice = _choice("연령군 열 생성: 5 / 10 / both(둘 다) / none [none]: ",
                             ("5", "10", "both", "none"), "none", read, emit)
            opts.setdefault("AGE_GROUPS", {}).pop(name, None)
            if choice != "none":
                invalid = "ERROR"
                if p["unrecognized_cells"]:
                    if _choice("해석 불가 값의 연령군을 NULL로 남기고 생성할까요? 원 연령은 보존합니다. [y/N]: ", ("y", "n"), "n", read, emit) != "y":
                        continue
                    invalid = "NULL"
                prefix = "AGE" if age_count == 1 else name
                opts["AGE_GROUPS"][name] = {"WIDTHS": [5, 10] if choice == "both" else [int(choice)],
                                                   "PREFIX": prefix, "OPEN_UPPER": 70, "INVALID_POLICY": invalid}
                emit("  생성할 열: " + ", ".join(f"{prefix}_{w}" for w in opts["AGE_GROUPS"][name]["WIDTHS"]))
    return ds


def validate_options(dataset):
    opts = dataset.meta.get("INIT_OPTIONS", {})
    if not isinstance(opts, dict) or set(opts) - {"SEX", "AGE", "AGE_INTEGER", "AGE_GROUPS", "UNIT_CONVERSIONS"}:
        raise ValueError("INIT_OPTIONS supports SEX, AGE, AGE_INTEGER, AGE_GROUPS and UNIT_CONVERSIONS tables only")
    for section, entries in opts.items():
        if not isinstance(entries, dict):
            raise ValueError(f"INIT_OPTIONS.{section} must be a table")
        for name, config in entries.items():
            column = next((c for c in dataset.columns if c.name == name), None)
            tag = "RESULT" if section == "UNIT_CONVERSIONS" else "SEX" if section == "SEX" else "AGE"
            if column is None or not column.has_tag(tag):
                raise ValueError(f"INIT_OPTIONS.{section}.{name} requires a {tag} column")
            allowed = ({"FROM_UNIT", "TO_UNIT", "FACTOR", "OFFSET", "REASON"} if section == "UNIT_CONVERSIONS" else
                       {"NORMALIZE"} if section == "SEX" else {"UNIT"} if section == "AGE" else
                       {"OUTPUT", "METHOD", "INVALID_POLICY"} if section == "AGE_INTEGER" else
                       {"WIDTHS", "PREFIX", "OPEN_UPPER", "INVALID_POLICY"})
            if not isinstance(config, dict) or set(config) - allowed:
                raise ValueError(f"Invalid INIT_OPTIONS.{section}.{name} options")
            if section == "SEX" and type(config.get("NORMALIZE", False)) is not bool:
                raise ValueError("NORMALIZE must be true or false")
            if section == "AGE" and config.get("UNIT", "a") not in {"a", "mo", "d"}:
                raise ValueError("AGE UNIT must be a, mo or d")
            if section == "AGE_INTEGER":
                if not isinstance(config.get("OUTPUT"), str) or not config["OUTPUT"].strip():
                    raise ValueError("AGE_INTEGER.OUTPUT must be a nonempty string")
                if config.get("METHOD", "FLOOR") != "FLOOR":
                    raise ValueError("AGE_INTEGER.METHOD must be FLOOR (completed years)")
                if config.get("INVALID_POLICY", "ERROR") not in {"ERROR", "NULL"}:
                    raise ValueError("AGE_INTEGER.INVALID_POLICY must be ERROR or NULL")
            if section == "UNIT_CONVERSIONS":
                from .units import unit_property, decimal_number
                source, target = config.get("FROM_UNIT"), config.get("TO_UNIT")
                unit_property(source)
                unit_property(target)
                if dataset.column_metadata(column).get("UNIT") != source:
                    raise ValueError(f"{name}: FROM_UNIT must match the declared original COLUMN.UNIT")
                if decimal_number(config.get("FACTOR"), label="conversion factor") <= 0:
                    raise ValueError("Conversion factor must be positive")
                decimal_number(config.get("OFFSET", 0), label="conversion offset")
                if not isinstance(config.get("REASON"), str) or not config["REASON"].strip():
                    raise ValueError("UNIT_CONVERSIONS requires a reviewed REASON for the conversion")
            if section == "AGE_GROUPS":
                widths = config.get("WIDTHS", [])
                if not isinstance(widths, list) or any(type(w) is not int or w not in {5, 10} for w in widths) or len(set(widths)) != len(widths):
                    raise ValueError("AGE_GROUPS.WIDTHS must be a unique list containing 5 and/or 10")
                upper = config.get("OPEN_UPPER", 70)
                if type(upper) is not int or upper <= 1 or any(upper % w for w in widths):
                    raise ValueError("OPEN_UPPER must exceed 1 and be divisible by each bin width")
                if config.get("INVALID_POLICY", "ERROR") not in {"ERROR", "NULL"}:
                    raise ValueError("INVALID_POLICY must be ERROR or NULL")
                if not isinstance(config.get("PREFIX", "AGE"), str) or not config.get("PREFIX", "AGE").strip():
                    raise ValueError("AGE_GROUPS.PREFIX must be a nonempty string")


def apply_options(dataset):
    """Apply only declared choices; never overwrite source or generated columns."""
    validate_options(dataset)
    ds = dataset.replace(df=dataset.df.copy(), columns=list(dataset.columns), meta=deepcopy(dataset.meta))
    opts = ds.meta.get("INIT_OPTIONS", {})
    transformations = []

    def add(name, values, tags, metadata):
        if name in ds.df.columns:
            raise ValueError(f"Init output column already exists: {name}; choose another PREFIX or rename the existing column")
        ds.df[name] = values
        ds.columns.append(ColumnSpec(name, name, tags))
        ds.meta.setdefault("COLUMN", {})[name] = {"TAGS": list(tags), **metadata}

    for name, config in opts.get("SEX", {}).items():
        if not config.get("NORMALIZE", False):
            continue
        col = next(c for c in ds.columns if c.name == name)
        targets = sex_targets(ds, col)
        original = ds.df[name].copy()
        add(name + "__raw", original, ("RAW", "STR"), {"SOURCE_COLUMN": name, "ROLE": "original source values before init normalization"})
        values = [t if t is not None else v for v, t in zip(original, targets)]
        ds.df[name] = values
        transformations.append({"operation": "NORMALIZE_SEX", "column": name, "raw_column": name + "__raw",
                                "changed_cells": sum(str(v) != str(t) for v, t in zip(original, values)),
                                "unmapped_cells": sum(cell_state(v) == STATE_VALUE and t is None for v, t in zip(original, targets))})
    for name, config in opts.get("AGE", {}).items():
        unit = config.get("UNIT", "a")
        if unit == "a":
            continue
        original = ds.df[name].copy()
        values = [age_input(v, unit) for v in original]
        if any(str(a) != str(b) for a, b in zip(original, values)):
            add(name + "__raw", original, ("RAW", "STR"), {"SOURCE_COLUMN": name, "ROLE": "original source values before declaring age unit"})
            ds.df[name] = values
            transformations.append({"operation": "DECLARE_AGE_UNIT", "column": name, "unit": unit,
                                    "changed_cells": sum(str(a) != str(b) for a, b in zip(original, values))})
    for name, config in opts.get("AGE_INTEGER", {}).items():
        invalid, values = 0, []
        for value in ds.df[name]:
            if cell_state(value) != STATE_VALUE:
                values.append(value)
                continue
            age = parse_age(value)
            if age is None:
                invalid += 1
                values.append(NULL)
            else:
                values.append(int(math.floor(age.years)))
        if invalid and config.get("INVALID_POLICY", "ERROR") != "NULL":
            raise ValueError(f"{name}: {invalid} invalid ages; correct values or explicitly set AGE_INTEGER.INVALID_POLICY='NULL'")
        output = config["OUTPUT"]
        add(output, values, ("NUM",),
            {"DERIVED_FROM": name, "UNIT": "a", "ROLE": "completed integer years",
             "AGE_INTEGER_METHOD": "FLOOR", "INVALID_POLICY": config.get("INVALID_POLICY", "ERROR"),
             "INVALID_CELLS": invalid})
        transformations.append({"operation": "CREATE_INTEGER_AGE", "column": output, "source_column": name,
                                "method": "FLOOR", "unit": "a", "invalid_cells": invalid,
                                "value_cells": sum(cell_state(v) == STATE_VALUE for v in values)})
    for name, config in opts.get("AGE_GROUPS", {}).items():
        for width in config.get("WIDTHS", []):
            output = config.get("PREFIX", "AGE") + "_" + str(width)
            upper = config.get("OPEN_UPPER", 70)
            invalid, values = 0, []
            for value in ds.df[name]:
                if cell_state(value) != STATE_VALUE:
                    values.append(value)
                    continue
                age = parse_age(value)
                if age is None:
                    invalid += 1
                    values.append(NULL)
                else:
                    values.append(age_band_label(age.years, width=width, open_upper=upper))
            if invalid and config.get("INVALID_POLICY", "ERROR") != "NULL":
                raise ValueError(f"{name}: {invalid} invalid ages; correct values or explicitly set INVALID_POLICY='NULL'")
            add(output, values, ("BY", "AGE_GROUP", "CATEGORY"),
                {"DERIVED_FROM": name, "AGE_BIN_WIDTH": width, "OPEN_UPPER": upper, "UNIT": "a",
                 "INTERVAL_RULE": "<1 means [0,1); L-U means [L,U+1); upper+ means [upper,infinity)",
                 "INVALID_POLICY": config.get("INVALID_POLICY", "ERROR"), "INVALID_CELLS": invalid})
            transformations.append({"operation": "CREATE_AGE_GROUP", "column": output, "source_column": name,
                                    "width": width, "open_upper": upper, "invalid_cells": invalid,
                                    "group_counts": dict(Counter(str(v) for v in values if cell_state(v) == STATE_VALUE))})
    for name, config in opts.get("UNIT_CONVERSIONS", {}).items():
        from .units import convert_cell
        original = ds.df[name].copy()
        values = [convert_cell(v, config["FACTOR"], offset=config.get("OFFSET", 0), label=name) for v in original]
        source_id = ds.meta.get("INFO", {}).get("SOURCE_SHA256", "")[:12]
        raw = name + "__raw_" + source_id
        original_meta = deepcopy(ds.column_metadata(name))
        add(raw, original, ("RAW", "STR"),
            {"SOURCE_COLUMN": name, "UNIT": config["FROM_UNIT"],
             "ID": str(original_meta.get("ID", name)) + ".raw." + source_id,
             "ROLE": "original source values before declared unit conversion"})
        ds.df[name] = values
        # Source-specific raw pointers belong to the transformation record. A
        # different raw column name must not make the same analyte incompatible.
        ds.meta.setdefault("COLUMN", {}).setdefault(name, {})["UNIT"] = config["TO_UNIT"]
        transformations.append({"operation": "CONVERT_UNIT", "column": name, "raw_column": raw,
                                "from_unit": config["FROM_UNIT"], "to_unit": config["TO_UNIT"],
                                "factor": str(config["FACTOR"]), "offset": str(config.get("OFFSET", 0)),
                                "reason": config["REASON"], "value_cells": sum(cell_state(v) == STATE_VALUE for v in original)})
    return ds, transformations

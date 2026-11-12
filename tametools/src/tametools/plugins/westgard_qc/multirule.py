"""Westgard multirule decisions on control z-scores.

Pure functions: no file, META or baseline handling. The caller supplies one row
per control measurement with its z-score against a fixed, declared baseline.

Every decision is made from the measurement's own run and earlier runs only.
Appending later runs therefore never changes the decision of an earlier run,
which is what lets a cumulative QC file be re-evaluated and compared safely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Iterable, Mapping

import pandas as pd


DEFAULT_WARNING_RULES = ("1_2s",)
DEFAULT_REJECT_RULES = ("1_3s", "2_2s", "R_4s", "4_1s", "10x")

SCOPES = ("BOTH", "WITHIN_MATERIAL", "ACROSS_MATERIAL")
TRIGGERS = ("ALWAYS", "1_2S")
TRIGGER_LIMIT = 2.0     # TRIGGER = "1_2S": a run is inspected when one of its controls is beyond this many SD

IN_CONTROL = "IN_CONTROL"
WARNING = "WARNING"
REJECT = "REJECT"
NOT_EVALUATED = "NOT_EVALUATED"
_SEVERITY = {IN_CONTROL: 0, NOT_EVALUATED: 1, WARNING: 2, REJECT: 3}

# Where a rule was met. A rule may be met in more than one way.
AT_POINT = "point"                       # this measurement alone
ACROSS_RUN = "across_run"                # consecutive runs of the same control material
WITHIN_RUN = "within_run"                # different materials (levels) of one run
ACROSS_RUN_MATERIAL = "across_run_material"  # consecutive runs, all materials together


@dataclass(frozen=True)
class Rule:
    name: str      # canonical spelling, e.g. 1_3s, 2_2s, R_4s, 4_1s, 10x, 2of3_2s
    kind: str      # single | consecutive | count | range
    n: int         # number of measurements inspected
    m: int         # measurements that must exceed the limit (count rules); otherwise n
    limit: float   # limit in SD units; 0 means "same side of the mean"
    error: str     # random | systematic


_NUMBER = r"(\d+(?:\.\d+)?)"
_SINGLE_OR_CONSECUTIVE = re.compile(rf"^(\d+)_{_NUMBER}s$", re.IGNORECASE)
_COUNT = re.compile(rf"^(\d+)of(\d+)_{_NUMBER}s$", re.IGNORECASE)
_RANGE = re.compile(rf"^R_{_NUMBER}s$", re.IGNORECASE)
_SAME_SIDE = re.compile(r"^(\d+)x$", re.IGNORECASE)


def _number_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def parse_rule(text: str) -> Rule:
    """Parse a Westgard rule name.

    Supported forms: ``1_ks`` (one measurement beyond k SD), ``N_ks`` (N consecutive
    beyond the same k SD limit), ``MofN_ks``, ``R_ks`` (range within a run; one
    beyond +k/2 SD and another beyond -k/2 SD) and ``Nx`` (N consecutive on one
    side of the mean).
    """
    name = str(text).strip()
    match = _RANGE.match(name)
    if match:
        width = float(match.group(1))
        if width <= 0:
            raise ValueError(f"Invalid QC rule: {text}")
        return Rule(f"R_{_number_text(width)}s", "range", 2, 2, width / 2, "random")
    match = _SAME_SIDE.match(name)
    if match:
        count = int(match.group(1))
        if count < 2:
            raise ValueError(f"Invalid QC rule: {text}")
        return Rule(f"{count}x", "consecutive", count, count, 0.0, "systematic")
    match = _COUNT.match(name)
    if match:
        hits, window, limit = int(match.group(1)), int(match.group(2)), float(match.group(3))
        if not 1 <= hits < window or limit <= 0:
            raise ValueError(f"Invalid QC rule: {text}")
        return Rule(f"{hits}of{window}_{_number_text(limit)}s", "count", window, hits, limit, "systematic")
    match = _SINGLE_OR_CONSECUTIVE.match(name)
    if match:
        count, limit = int(match.group(1)), float(match.group(2))
        if count < 1 or limit <= 0:
            raise ValueError(f"Invalid QC rule: {text}")
        if count == 1:
            return Rule(f"1_{_number_text(limit)}s", "single", 1, 1, limit, "random")
        return Rule(f"{count}_{_number_text(limit)}s", "consecutive", count, count, limit, "systematic")
    raise ValueError(f"Unknown QC rule: {text}. Examples: 1_2s, 1_3s, 2_2s, R_4s, 4_1s, 10x, 2of3_2s")


def parse_rules(names: Iterable[str]) -> list[Rule]:
    rules = [parse_rule(name) for name in names]
    if len({rule.name for rule in rules}) != len(rules):
        raise ValueError("QC rules must be distinct")
    return rules


ACTIONS = ("REJECT", "WARNING", "OFF")
AFTER_REJECT = ("RESTART", "SKIP", "CONTINUE")
RANGE_FORMS = ("COUNT", "RANGE")
_PLAN_KEYS = {"RULES", "WARNING_RULES", "SCOPE", "TRIGGER", "AFTER_REJECT", "RULE_SETTINGS"}
_RULE_OPTION_KEYS = {"ACTION", "SCOPE", "FORM"}


@dataclass(frozen=True)
class RulePlan:
    """The rules in force for one analyte, after every level of configuration."""
    rules: tuple[str, ...]           # rejection rules, canonical names, in evaluation order
    warning_rules: tuple[str, ...]
    scope: str                       # default scope of rules over consecutive measurements
    trigger: str
    rule_scopes: Mapping[str, str]   # per-rule exceptions to ``scope``
    after_reject: str = "RESTART"    # what a rejected run does to later consecutive counts
    rule_forms: Mapping[str, str] = field(default_factory=dict)  # range rules read as COUNT (default) or RANGE

    def scope_for(self, name: str) -> str:
        """The scope in force for one rule; empty for rules that have none."""
        if parse_rule(name).kind not in {"consecutive", "count"}:
            return ""
        return self.rule_scopes.get(name, self.scope)

    def as_dict(self) -> dict:
        return {"RULES": list(self.rules), "WARNING_RULES": list(self.warning_rules), "SCOPE": self.scope,
                "TRIGGER": self.trigger, "AFTER_REJECT": self.after_reject, "RULE_SCOPES": dict(self.rule_scopes),
                "RULE_FORMS": dict(self.rule_forms)}


def _rule_names(value, key: str) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")  # CLI form: --option RULES=1_3s,2_2s,R_4s
    if not isinstance(value, (list, tuple)) or any(not str(item).strip() for item in value):
        raise ValueError(f"{key} must be a list of QC rule names, e.g. [\"1_3s\", \"2_2s\", \"R_4s\"]")
    return [rule.name for rule in parse_rules(str(item).strip() for item in value)]


def rule_plan(settings: Mapping | None = None, *, result_id: str | None = None) -> RulePlan:
    """Resolve the rule configuration for one analyte.

    Three levels, each optional, the later one winning:

    1. ``RULES`` / ``WARNING_RULES`` / ``SCOPE`` / ``TRIGGER`` / ``AFTER_REJECT``: the set for every analyte.
    2. ``RULE_SETTINGS.<rule>``: one rule at a time. ``ACTION`` = REJECT, WARNING or OFF;
       ``SCOPE`` = BOTH, WITHIN_MATERIAL or ACROSS_MATERIAL for that rule only;
       ``FORM`` = COUNT or RANGE for a range rule such as R_4s.
    3. ``BY_RESULT.<result id>``: the same keys again for a single analyte.

    Listing a rule in ``RULES`` makes it a rejection rule even if it was a warning
    by default, and the other way round for ``WARNING_RULES``.
    """
    cfg = {str(key).upper(): value for key, value in (settings or {}).items()}
    state = {"reject": list(DEFAULT_REJECT_RULES), "warn": list(DEFAULT_WARNING_RULES),
             "scope": "BOTH", "trigger": "ALWAYS", "after_reject": "RESTART", "scopes": {}, "forms": {}}

    def apply(layer: Mapping, where: str) -> None:
        layer = {str(key).upper(): value for key, value in layer.items()}
        unknown = set(layer) - _PLAN_KEYS
        if unknown:
            raise ValueError(f"Unknown {where} option: " + ", ".join(sorted(unknown)))
        reject = _rule_names(layer["RULES"], "RULES") if layer.get("RULES") is not None else None
        warn = _rule_names(layer["WARNING_RULES"], "WARNING_RULES") if layer.get("WARNING_RULES") is not None else None
        if reject is not None and warn is not None and set(reject) & set(warn):
            raise ValueError("A QC rule cannot be both a warning and a rejection rule: " + ", ".join(sorted(set(reject) & set(warn))))
        if warn is not None:
            state["warn"] = warn
            state["reject"] = [name for name in state["reject"] if name not in warn]
        if reject is not None:
            state["reject"] = reject
            state["warn"] = [name for name in state["warn"] if name not in reject]
        for key, allowed in (("SCOPE", SCOPES), ("TRIGGER", TRIGGERS), ("AFTER_REJECT", AFTER_REJECT)):
            if layer.get(key) is not None:
                value = str(layer[key]).strip().upper()
                if value not in allowed:
                    raise ValueError(f"{key} must be one of: " + ", ".join(allowed))
                state[key.lower()] = value
        per_rule = layer.get("RULE_SETTINGS") or {}
        if not isinstance(per_rule, Mapping):
            raise ValueError(f"{where}.RULE_SETTINGS must be a table keyed by rule name")
        for raw_name, options in per_rule.items():
            rule = parse_rule(raw_name)
            if not isinstance(options, Mapping):
                raise ValueError(f"RULE_SETTINGS.{rule.name} must be a table with ACTION, SCOPE and/or FORM")
            options = {str(key).upper(): value for key, value in options.items()}
            if set(options) - _RULE_OPTION_KEYS:
                raise ValueError(f"Unknown RULE_SETTINGS.{rule.name} option: " + ", ".join(sorted(set(options) - _RULE_OPTION_KEYS)))
            if options.get("ACTION") is not None:
                action = str(options["ACTION"]).strip().upper()
                if action not in ACTIONS:
                    raise ValueError(f"RULE_SETTINGS.{rule.name}.ACTION must be one of: " + ", ".join(ACTIONS))
                state["reject"] = [name for name in state["reject"] if name != rule.name]
                state["warn"] = [name for name in state["warn"] if name != rule.name]
                if action != "OFF":
                    state["reject" if action == "REJECT" else "warn"].append(rule.name)
            if options.get("SCOPE") is not None:
                value = str(options["SCOPE"]).strip().upper()
                if value not in SCOPES:
                    raise ValueError(f"RULE_SETTINGS.{rule.name}.SCOPE must be one of: " + ", ".join(SCOPES))
                if rule.kind not in {"consecutive", "count"}:
                    raise ValueError(f"{rule.name} has no SCOPE: it looks at one measurement or at one run")
                state["scopes"][rule.name] = value
            if options.get("FORM") is not None:
                value = str(options["FORM"]).strip().upper()
                if value not in RANGE_FORMS:
                    raise ValueError(f"RULE_SETTINGS.{rule.name}.FORM must be one of: " + ", ".join(RANGE_FORMS))
                if rule.kind != "range":
                    raise ValueError(f"{rule.name} has no FORM: only range rules such as R_4s do")
                state["forms"][rule.name] = value

    by_result = cfg.pop("BY_RESULT", None) or {}
    if not isinstance(by_result, Mapping):
        raise ValueError("BY_RESULT must be a table keyed by result id")
    apply(cfg, "QC rule")
    if result_id is not None and result_id in by_result:
        if not isinstance(by_result[result_id], Mapping):
            raise ValueError(f"BY_RESULT.{result_id} must be a table")
        apply(by_result[result_id], f"BY_RESULT.{result_id}")
    active = {*state["reject"], *state["warn"]}
    return RulePlan(rules=tuple(state["reject"]), warning_rules=tuple(state["warn"]), scope=state["scope"],
                    trigger=state["trigger"], after_reject=state["after_reject"],
                    rule_scopes={name: value for name, value in state["scopes"].items() if name in active},
                    rule_forms={name: value for name, value in state["forms"].items() if name in active})


def _side(z: float, limit: float) -> int:
    """+1 above +limit, -1 below -limit, 0 inside (or exactly on the mean for limit 0)."""
    if z > limit:
        return 1
    if z < -limit:
        return -1
    return 0


def _same_side(values: list[float], limit: float) -> int:
    sides = {_side(value, limit) for value in values}
    return sides.pop() if len(sides) == 1 else 0


def evaluate_multirule(
    points: pd.DataFrame,
    *,
    rules: Iterable[str] | None = None,
    warning_rules: Iterable[str] | None = None,
    scope: str = "BOTH",
    trigger: str = "ALWAYS",
    rule_scopes: Mapping[str, str] | None = None,
    after_reject: str = "RESTART",
    rule_forms: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Apply warning and rejection rules to control measurements, run by run.

    ``points`` needs these columns (one row per control measurement):

    - ``group``: what shares runs, normally analyte + instrument.
    - ``run``: run identifier within the group.
    - ``order``: sortable position of the run in time.
    - ``material``: one control chart, normally level + lot + baseline.
    - ``z``: (value - baseline mean) / baseline SD; NaN when there is no result.

    ``scope`` is the default for every rule over consecutive measurements;
    ``rule_scopes`` overrides it for individual rules, e.g. ``{"10x": "WITHIN_MATERIAL"}``.

    What ends a sequence of consecutive measurements:

    - a missing result (a run that did not measure the material at all does not);
    - a rejected run, according to ``after_reject``: ``RESTART`` (default) starts
      every count of the analyte again after it, ``SKIP`` leaves only that run's
      values out, ``CONTINUE`` keeps counting them.

    ``rule_forms`` selects how a range rule is read: ``COUNT`` (one value beyond
    +2 SD and another beyond -2 SD, the original R_4s) or ``RANGE`` (highest
    minus lowest of the run is at least 4 SD).

    Returns a frame on the same index with ``violation``, ``warning``,
    ``rule_scope``, ``error_type``, ``decision`` and ``run_decision``.
    """
    required = {"group", "run", "order", "material", "z"}
    if not required <= set(points.columns):
        raise ValueError("QC points need columns: " + ", ".join(sorted(required)))
    scope, trigger, after_reject = str(scope).strip().upper(), str(trigger).strip().upper(), str(after_reject).strip().upper()
    if scope not in SCOPES:
        raise ValueError("SCOPE must be one of: " + ", ".join(SCOPES))
    if trigger not in TRIGGERS:
        raise ValueError("TRIGGER must be one of: " + ", ".join(TRIGGERS))
    if after_reject not in AFTER_REJECT:
        raise ValueError("AFTER_REJECT must be one of: " + ", ".join(AFTER_REJECT))
    reject = parse_rules(DEFAULT_REJECT_RULES if rules is None else rules)
    warn = parse_rules(DEFAULT_WARNING_RULES if warning_rules is None else warning_rules)
    if {rule.name for rule in reject} & {rule.name for rule in warn}:
        raise ValueError("A QC rule cannot be both a warning and a rejection rule")
    every_rule = [*warn, *reject]
    scope_of = {rule.name: scope for rule in every_rule}
    for name, value in (rule_scopes or {}).items():
        rule = parse_rule(name)
        value = str(value).strip().upper()
        if rule.name not in scope_of:
            raise ValueError(f"SCOPE given for a QC rule that is not in use: {name}")
        if value not in SCOPES:
            raise ValueError(f"SCOPE for {rule.name} must be one of: " + ", ".join(SCOPES))
        if rule.kind not in {"consecutive", "count"}:
            raise ValueError(f"{rule.name} has no SCOPE: it looks at one measurement or at one run")
        scope_of[rule.name] = value
    form_of = {rule.name: "COUNT" for rule in every_rule if rule.kind == "range"}
    for name, value in (rule_forms or {}).items():
        rule = parse_rule(name)
        value = str(value).strip().upper()
        if rule.name not in form_of:
            raise ValueError(f"FORM given for a rule that is not a range rule in use: {name}")
        if value not in RANGE_FORMS:
            raise ValueError(f"FORM for {rule.name} must be one of: " + ", ".join(RANGE_FORMS))
        form_of[rule.name] = value
    warning_names = [rule.name for rule in warn]
    reject_names = [rule.name for rule in reject]

    z_of = {index: float(value) if value is not None and not pd.isna(value) else math.nan
            for index, value in points["z"].items()}
    hits: dict[object, dict[str, set[str]]] = {index: {} for index in points.index}
    run_of: dict[object, tuple] = {}

    def hit(index, rule: Rule, where: str) -> None:
        hits[index].setdefault(rule.name, set()).add(where)

    for group_key, group in points.groupby("group", sort=False, dropna=False):
        if group.duplicated(["run", "material"]).any():
            raise ValueError("Duplicate QC measurement for one run and control material")
        run_order = group.groupby("run", sort=False)["order"].min()
        runs = sorted(run_order.index, key=lambda run: (run_order[run], str(run)))
        position = {run: number for number, run in enumerate(runs)}
        material_of = group["material"].to_dict()
        by_run: dict[int, list] = {number: [] for number in range(len(runs))}
        for index in sorted(group.index, key=lambda index: (position[group.at[index, "run"]], str(material_of[index]))):
            by_run[position[group.at[index, "run"]]].append(index)
            run_of[index] = (group_key, group.at[index, "run"])

        # What still counts toward consecutive-run rules, as of the run being judged.
        earlier: dict[object, list] = {}   # material -> earlier measurements of that material
        earlier_runs: list[int] = []       # earlier runs usable for windows across materials

        for number in range(len(runs)):
            everything = by_run[number]
            current = [index for index in everything if not math.isnan(z_of[index])]
            values = [z_of[index] for index in current]
            materials = frozenset(material_of[index] for index in everything)
            for rule in every_rule:
                within_material = scope_of[rule.name] in {"BOTH", "WITHIN_MATERIAL"}
                across_material = scope_of[rule.name] in {"BOTH", "ACROSS_MATERIAL"}
                if rule.kind == "single":
                    for index in current:
                        if abs(z_of[index]) > rule.limit:
                            hit(index, rule, AT_POINT)
                    continue
                if rule.kind == "range":
                    if len(current) < 2:
                        continue
                    if form_of[rule.name] == "RANGE":
                        if max(values) - min(values) >= 2 * rule.limit:
                            for index in current:
                                if z_of[index] in (max(values), min(values)):
                                    hit(index, rule, WITHIN_RUN)
                    elif max(values) > rule.limit and min(values) < -rule.limit:
                        for index in current:
                            if _side(z_of[index], rule.limit):
                                hit(index, rule, WITHIN_RUN)
                    continue
                # The same control material over consecutive runs, attributed to this run's
                # measurement, which completes the sequence.
                # A run of n needs all n values. "m of n" is decided as soon as m values exist:
                # whatever the missing ones turn out to be, they cannot undo it.
                enough = rule.n if rule.kind == "consecutive" else rule.m
                if within_material:
                    for index in current:
                        before = earlier.get(material_of[index], [])
                        if len(before) < enough - 1:
                            continue
                        window = [z_of[other] for other in before[max(0, len(before) - (rule.n - 1)):]] + [z_of[index]]
                        if rule.kind == "consecutive":
                            if _same_side(window, rule.limit):
                                hit(index, rule, ACROSS_RUN)
                        else:
                            last = _side(window[-1], rule.limit)
                            if last and sum(_side(value, rule.limit) == last for value in window) >= rule.m:
                                hit(index, rule, ACROSS_RUN)
                # Different control materials together. Windows always cover whole runs.
                if not across_material or len(current) < 2:
                    continue
                if enough <= len(current):
                    for side in (1, -1):
                        beyond = [index for index in current if _side(z_of[index], rule.limit) == side]
                        if len(beyond) >= enough:
                            for index in beyond:
                                hit(index, rule, WITHIN_RUN)
                    continue
                if rule.kind != "consecutive" or len(current) != len(everything):
                    continue
                run_count = math.ceil(rule.n / len(current))
                if len(earlier_runs) < run_count - 1:
                    continue
                window_runs = earlier_runs[len(earlier_runs) - (run_count - 1):]
                if any(frozenset(material_of[index] for index in by_run[other]) != materials for other in window_runs):
                    continue
                window = [z_of[index] for other in window_runs for index in by_run[other]] + values
                if _same_side(window, rule.limit):
                    for index in current:
                        hit(index, rule, ACROSS_RUN_MATERIAL)

            # Classic manual use: the rules are inspected only when a control of the run lies beyond 2 SD.
            # The excursion itself decides. Whether 1_2s is a warning, a rejection rule or not in use
            # must not switch the other rules off.
            if trigger == "1_2S" and not any(abs(z_of[index]) > TRIGGER_LIMIT for index in current):
                for index in everything:
                    hits[index] = {}
            rejected = any(name in hits[index] for index in everything for name in reject_names)

            if rejected and after_reject == "RESTART":
                earlier.clear()
                earlier_runs.clear()
                continue
            keep = not (rejected and after_reject == "SKIP")
            for index in everything:
                if math.isnan(z_of[index]):
                    earlier[material_of[index]] = []          # a missing result breaks that material's sequence
                elif keep:
                    earlier.setdefault(material_of[index], []).append(index)
            if len(current) != len(everything):
                earlier_runs.clear()                           # ... and the sequence of whole runs
            elif keep:
                earlier_runs.append(number)

    error_of = {rule.name: rule.error for rule in reject}
    rows = {}
    for index in points.index:
        if math.isnan(z_of[index]):
            rows[index] = dict(violation="NE", warning="NE", rule_scope="NE", error_type="NE", decision=NOT_EVALUATED)
            continue
        violated = [name for name in reject_names if name in hits[index]]
        warned = [name for name in warning_names if name in hits[index]]
        errors = sorted({error_of[name] for name in violated})
        scopes = "; ".join(f"{name}:{'+'.join(sorted(hits[index][name]))}" for name in [*warned, *violated])
        rows[index] = dict(
            violation=", ".join(violated) or "NONE",
            warning=", ".join(warned) or "NONE",
            rule_scope=scopes or "NONE",
            error_type="+".join(errors) or "NONE",
            decision=REJECT if violated else WARNING if warned else IN_CONTROL,
        )
    result = pd.DataFrame.from_dict(rows, orient="index",
        columns=["violation", "warning", "rule_scope", "error_type", "decision"]).reindex(points.index)
    worst: dict[tuple, str] = {}
    for index, decision in result["decision"].items():
        key = run_of[index]
        if key not in worst or _SEVERITY[decision] > _SEVERITY[worst[key]]:
            worst[key] = decision
    result["run_decision"] = [worst[run_of[index]] for index in result.index]
    return result

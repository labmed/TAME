"""Corrective-action and review records for QC results.

The source file holds measurements and the result file is recomputed at every
sync, so what people did about a rejected run cannot live in either. It is kept
in the project file as an append-only record (see ``tametools.sync.add_record``)
and joined to the result when the report is written.

- An ACTION record says what was found and done about one run of one or more analytes.
- A REVIEW record says who reviewed the QC of a period.
- Records are never edited. A correction is a new record that supersedes an earlier one.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from tametools.plugin_api import (Form, SyncError, add_record, ask, data_fingerprint, field, form, read_result, read_tame,
                                  recordable, rerender_report, sync_config, sync_records, sync_state, verify_records)

from .multirule import REJECT

ACTION, REVIEW = "ACTION", "REVIEW"


class NothingToRecord(SyncError):
    """Asked what to record an action about, and no rejected run is waiting for one."""


def _text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()


def effective(records: list[Mapping], kind: str | None = None) -> list[Mapping]:
    """Records in force: of the given kind and not superseded by a later record."""
    replaced = {record.get("SUPERSEDES") for record in records if record.get("SUPERSEDES")}
    return [record for record in records if record.get("ID") not in replaced and (kind is None or record.get("KIND") == kind)]


def covers(record: Mapping, *, instrument: str, run: str, result_id: str) -> bool:
    """Does an ACTION record speak about this run of this analyte?"""
    results = record.get("RESULTS") or []
    return (record.get("KIND") == ACTION and _text(record.get("RUN")) == run
            and (not results or result_id in results)
            and (not _text(record.get("INSTRUMENT")) or _text(record.get("INSTRUMENT")) == instrument))


def rejected_runs(result: pd.DataFrame) -> list[dict]:
    """One entry per rejected run of an analyte (oldest first), with the levels and rules involved."""
    needed = {"result_id", "instrument", "run", "run_decision", "violation", "level", "time"}
    if not needed <= set(result.columns):
        return []
    frame = result.loc[result["run_decision"].map(_text).eq(REJECT)]
    entries = []
    for (instrument, run, result_id), group in frame.groupby(["instrument", "run", "result_id"], sort=False):
        rules = list(dict.fromkeys(name for cell in group["violation"] for name in _text(cell).split(", ") if name and name not in {"NONE", "NE"}))
        name = _text(group["source_result_column"].iloc[0]) if "source_result_column" in group.columns else ""
        entries.append({"instrument": _text(instrument), "run": _text(run), "result_id": _text(result_id), "name": name or _text(result_id),
                        "time": _text(group["time"].min()), "levels": sorted(_text(level) for level in group["level"]), "rules": rules})
    return sorted(entries, key=lambda item: (item["time"], item["result_id"]))


def open_rejections(result: pd.DataFrame, records: list[Mapping]) -> list[dict]:
    """Rejected runs that no ACTION record in force covers."""
    actions = effective(records, ACTION)
    return [entry for entry in rejected_runs(result)
            if not any(covers(record, instrument=entry["instrument"], run=entry["run"], result_id=entry["result_id"]) for record in actions)]


def review_covers(records: list[Mapping], first: str, last: str) -> bool:
    """Is every day from ``first`` to ``last`` (YYYY-MM-DD) inside the period of one REVIEW record in force?"""
    return any(_text(record.get("FROM")) <= first and last <= _text(record.get("TO")) for record in effective(records, REVIEW))


def qc_notices(context: Mapping) -> list[str]:
    """Reminder printed by every sync: rejected runs that still have no action record."""
    result = context.get("result")
    if result is None:
        return []
    waiting = open_rejections(result.df, list(context.get("records") or []))
    if not waiting:
        return []
    latest = waiting[-1]
    return [f"조치 기록이 없는 거부 run {len(waiting)}건 (최근: {latest['time'][:16].replace('T', ' ')} {latest['name']}). "
            "목록: tametools qc-action PROJECT --list · 창으로 기록: tametools qc-action PROJECT --dialog"]


def _current_result(project_path: Path):
    """The project, its records and the result of the last sync (required to check what a record refers to)."""
    project = read_tame(project_path)
    config = sync_config(project, project_path)
    if config.result is None or not config.result.exists():
        raise SyncError("결과 파일이 없습니다. 먼저 tametools sync 를 실행하세요")
    result = read_result(config.result)
    if sync_state(project).get("RESULT_SHA256") != data_fingerprint(result):
        raise SyncError("결과 파일이 마지막 동기화의 것이 아닙니다. 먼저 tametools sync 를 실행하세요")
    return project, result


def _match_run(frame: pd.DataFrame, text: str) -> str:
    """Exact run id, or the only run that starts with ``text`` (so a date is enough for one run a day)."""
    runs = list(dict.fromkeys(frame["run"].map(_text)))
    if text in runs:
        return text
    starting = [run for run in runs if run.startswith(text)]
    if len(starting) == 1:
        return starting[0]
    recent = ", ".join(runs[-5:])
    if not starting:
        raise SyncError(f"결과에 run '{text}' 이(가) 없습니다. 최근 run: {recent}")
    raise SyncError(f"'{text}' (으)로 시작하는 run 이 여러 개입니다: " + ", ".join(starting[:8]))


def action_status(project_path: str | Path) -> dict:
    """Open rejections and the records kept so far (for ``qc-action --list``)."""
    project, result = _current_result(Path(project_path))
    records = sync_records(project)
    return {"open": open_rejections(result.df, records), "rejected": rejected_runs(result.df), "records": records,
            "broken": verify_records(records)}


def add_action(project_path: str | Path, *, run: str = "", results: list[str] | None = None, instrument: str = "",
               cause: str, action: str, outcome: str = "", patient_impact: str = "", by: str, supersedes: int = 0,
               now: str | None = None) -> dict:
    """Record what was found and done about one run. Returns the record and what is still open."""
    path = Path(project_path)
    project, result = _current_result(path)
    frame = result.df
    records = sync_records(project)
    earlier = next((record for record in records if record.get("ID") == supersedes), None) if supersedes else None
    if earlier is not None and not run:      # a correction keeps the target unless a new one is given
        run, instrument = _text(earlier.get("RUN")), instrument or _text(earlier.get("INSTRUMENT"))
        results = results or list(earlier.get("RESULTS") or [])
    if not _text(run):
        raise SyncError("어느 run 에 대한 조치인지 --run 으로 지정하세요 (목록: --list)")
    if not _text(cause) or not _text(action):
        raise SyncError("조치 기록에는 원인(--cause)과 조치 내용(--action)이 모두 필요합니다. 원인을 모르면 '원인 미상'처럼 적으세요")
    run = _match_run(frame, _text(run))
    rows = frame.loc[frame["run"].map(_text).eq(run)]
    if _text(instrument):
        rows = rows.loc[rows["instrument"].map(_text).eq(_text(instrument))]
        if rows.empty:
            raise SyncError(f"run {run} 에 장비 '{instrument}' 의 결과가 없습니다")
    elif rows["instrument"].map(_text).nunique() > 1:
        raise SyncError(f"run {run} 에 장비가 여러 대 있습니다. --instrument 로 지정하세요: "
                        + ", ".join(sorted(set(rows["instrument"].map(_text)))))
    names = {}
    for _, row in rows.iterrows():                       # accept the id ("glucose") or the name the laboratory uses ("Glucose")
        names[_text(row["result_id"]).lower()] = _text(row["result_id"])
        if "source_result_column" in rows.columns:
            names[_text(row["source_result_column"]).lower()] = _text(row["result_id"])
    chosen = []
    for item in results or []:
        key = _text(item).lower()
        if key not in names:
            raise SyncError(f"run {run} 에 검사 '{item}' 이(가) 없습니다. 있는 검사: " + ", ".join(sorted(set(names.values()))))
        if names[key] not in chosen:
            chosen.append(names[key])
    targeted = rows if not chosen else rows.loc[rows["result_id"].map(_text).isin(chosen)]
    fields = {"RUN": run, "INSTRUMENT": _text(instrument), "RESULTS": chosen, "CAUSE": _text(cause), "ACTION": _text(action),
              "OUTCOME": _text(outcome), "PATIENT_IMPACT": _text(patient_impact)}
    record = add_record(path, ACTION, fields, by=by, supersedes=supersedes, now=now)
    report = rerender_report(path, now=now)
    remaining = open_rejections(frame, [*records, record])
    return {"record": record, "report": report, "open": remaining,
            "rejected_in_target": bool(targeted["run_decision"].map(_text).eq(REJECT).any())}


def _day(text: str, name: str) -> str:
    try:
        return dt.date.fromisoformat(_text(text)).isoformat()
    except ValueError as exc:
        raise SyncError(f"{name} 는 YYYY-MM-DD 형식의 날짜여야 합니다: {text}") from exc


def add_review(project_path: str | Path, *, start: str, end: str, by: str, note: str = "", supersedes: int = 0,
               now: str | None = None) -> dict:
    """Record that the QC of a period (both days included) was reviewed."""
    path = Path(project_path)
    project, result = _current_result(path)
    first, last = _day(start, "--from"), _day(end, "--to")
    if first > last:
        raise SyncError("검토 기간의 시작일이 종료일보다 늦습니다")
    records = sync_records(project)
    days = result.df["time"].map(lambda value: _text(value)[:10])
    inside = result.df.loc[days.ge(first) & days.le(last)]
    waiting = [entry for entry in open_rejections(result.df, records) if first <= entry["time"][:10] <= last]
    fields = {"FROM": first, "TO": last, "NOTE": _text(note), "RUNS": int(inside["run"].nunique()),
              "REJECTED_RUNS": int(len([entry for entry in rejected_runs(result.df) if first <= entry["time"][:10] <= last])),
              "OPEN_AT_REVIEW": int(len(waiting))}
    record = add_record(path, REVIEW, fields, by=by, supersedes=supersedes, now=now)
    return {"record": record, "report": rerender_report(path, now=now), "open_in_period": waiting}


# ----------------------------------------------------------------------------------------------------------------------
# The same records, collected by asking (a dialog window, or prompts in a terminal: tametools.dialogs)
# ----------------------------------------------------------------------------------------------------------------------

def action_targets(waiting: list[dict]) -> list[dict]:
    """What one action record can be about, most recent first: an analyte of a rejected run, or the analytes rejected together."""
    several = len({entry["instrument"] for entry in waiting}) > 1
    runs: dict[tuple[str, str], list[dict]] = {}
    for entry in waiting:
        runs.setdefault((entry["instrument"], entry["run"]), []).append(entry)
    targets = []
    for (instrument, run), entries in reversed(list(runs.items())):
        when = entries[0]["time"][:16].replace("T", " ") + (f" · {instrument}" if several and instrument else "")
        if len(entries) > 1:
            targets.append({"label": f"{when} · {', '.join(entry['name'] for entry in entries)} (한 기록으로 함께)",
                            "run": run, "instrument": instrument, "results": [entry["result_id"] for entry in entries]})
        for entry in entries:
            targets.append({"label": f"{when} · {entry['name']} · 수준 {', '.join(entry['levels'])} · {', '.join(entry['rules'])}",
                            "run": run, "instrument": instrument, "results": [entry["result_id"]]})
    return targets


def action_form(targets: list[dict], *, about: str = "") -> Form:
    fields = []
    if targets:
        labels = [target["label"] for target in targets]
        # With several to choose from nothing is preselected: a record must not land on the wrong run by pressing 저장.
        fields.append(field("target", "대상", "choice", choices=labels, required=True,
                                    default=labels[0] if len(labels) == 1 else None))
    fields += [
        field("cause", "원인", required=True, hint="모르면 '원인 미상'처럼 적습니다"),
        field("action", "조치 내용", "multiline", required=True),
        field("outcome", "조치 후 결과", hint="예: 재측정 결과"),
        field("patient_impact", "환자 결과 영향", hint="예: 보고 보류, 재검, 영향 없음"),
        field("by", "기록자", required=True),
    ]
    return form("QC 조치 기록", fields, intro=about, submit="저장")


def ask_action(project_path: str | Path, *, run: str = "", results: list[str] | None = None, instrument: str = "",
               cause: str = "", action: str = "", outcome: str = "", patient_impact: str = "", by: str = "",
               supersedes: int = 0) -> dict | None:
    """Ask for an action record, starting from what is already given. Returns the arguments for ``add_action``, or None if cancelled."""
    _, records = recordable(project_path, ACTION, supersedes)      # before anyone types: can it be saved at all?
    start = {"cause": cause, "action": action, "outcome": outcome, "patient_impact": patient_impact, "by": by}
    targets: list[dict] = []
    if supersedes and not _text(run):
        earlier = next(record for record in records if record.get("ID") == supersedes)
        about = (f"조치 기록 #{supersedes} (run {earlier.get('RUN')} · {', '.join(earlier.get('RESULTS') or []) or '전체 검사'}) 을(를) 정정합니다. "
                 "앞 기록은 지워지지 않고 '대체됨'으로 남습니다.")
        for key in ("cause", "action", "outcome", "patient_impact"):
            start[key] = _text(start[key]) or _text(earlier.get(key.upper()))
    elif _text(run):
        about = f"run {_text(run)} · {', '.join(results or []) or '전체 검사'}"
    else:
        waiting = action_status(project_path)["open"]
        targets = action_targets(waiting)
        if not targets:
            raise NothingToRecord("조치 기록이 없는 거부 run 이 없습니다. 다른 run 에 대해 남기려면 --run 으로 지정하세요")
        about = f"조치 기록이 없는 거부 run {len(waiting)}건"
    answers = ask(action_form(targets, about=about), {key: value for key, value in start.items() if _text(value)})
    if answers is None:
        return None
    chosen = next((target for target in targets if target["label"] == answers.get("target")), None)
    return {"run": chosen["run"] if chosen else run, "results": list(chosen["results"] if chosen else results or []),
            "instrument": chosen["instrument"] if chosen else instrument, "cause": answers["cause"], "action": answers["action"],
            "outcome": answers["outcome"], "patient_impact": answers["patient_impact"], "by": answers["by"], "supersedes": supersedes}


def ask_review(project_path: str | Path, *, start: str = "", end: str = "", by: str = "", note: str = "",
               supersedes: int = 0) -> dict | None:
    """Ask for a review record. Returns the arguments for ``add_review``, or None if cancelled."""
    _, records = recordable(project_path, REVIEW, supersedes)
    _, result = _current_result(Path(project_path))
    days = sorted(day for day in result.df["time"].map(lambda value: _text(value)[:10]) if day)
    if not days:
        raise SyncError("검토할 결과가 없습니다")
    first, last = days[0], days[-1]
    earlier = next((record for record in records if record.get("ID") == supersedes), {}) if supersedes else {}
    reviewed = max((_text(record.get("TO")) for record in effective(records, REVIEW)), default="")
    resume = (dt.date.fromisoformat(reviewed) + dt.timedelta(days=1)).isoformat() if reviewed else ""   # carry on after the last review
    values = {"start": _text(start) or _text(earlier.get("FROM")) or (resume if first <= resume <= last else first),
              "end": _text(end) or _text(earlier.get("TO")) or last, "by": by, "note": _text(note) or _text(earlier.get("NOTE"))}
    about = f"결과 기간 {first} ~ {last} · 조치 기록이 없는 거부 run {len(open_rejections(result.df, records))}건"
    if supersedes:
        about = f"검토 기록 #{supersedes} 을(를) 정정합니다. " + about
    answers = ask(form("QC 검토 기록", [
        field("start", "검토 시작일", "date", required=True),
        field("end", "검토 종료일", "date", required=True),
        field("by", "검토자", required=True),
        field("note", "비고", "multiline"),
    ], intro=about, submit="저장"), values)
    if answers is None:
        return None
    return {"start": answers["start"], "end": answers["end"], "by": answers["by"], "note": answers["note"], "supersedes": supersedes}

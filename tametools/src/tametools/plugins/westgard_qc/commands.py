"""The commands this plugin adds to the command line: ``tametools qc-action`` and ``tametools qc-review``.

They are registered like the analysis itself (``register_command``), so nothing about QC records is
written in tametools' own command-line code.
"""
from __future__ import annotations

import json

from tametools.plugin_api import read_tame, register_command, sync_records, verify_records, wanted

from .actions import NothingToRecord, action_status, add_action, add_review, ask_action, ask_review, effective

_REPORT = {"WRITTEN": "갱신했습니다", "STALE": "다음 tametools sync 때 갱신됩니다", "NONE": "설정되어 있지 않습니다"}


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _dialog_arguments(parser, what: str) -> None:
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--dialog", dest="dialog", action="store_true", default=None,
                        help=f"Ask for {what} in a dialog window (prompts in the terminal where no window can open).")
    choice.add_argument("--no-dialog", dest="dialog", action="store_false",
                        help="Never ask. Default: ask only when something required is missing and a person is at the terminal.")


def _ask(args, *, complete: bool) -> bool:
    """Should the record be asked for? ``--json`` is a program asking: it is never interrupted unless --dialog says so."""
    return wanted(args.dialog if args.dialog is not None or not args.json else False, needed=not complete)


def _action_arguments(parser) -> None:
    parser.add_argument("path", metavar="PROJECT", help="Project .tame file with a META[SYNC] section.")
    parser.add_argument("--list", action="store_true", help="List rejected runs without an action record, and the records kept so far.")
    parser.add_argument("--run", help="Run the record is about: its id, or the start of it (a date is enough for one run a day).")
    parser.add_argument("--result", action="append", default=[], metavar="TEST", help="Analyte id or name. Repeatable or comma-separated. Default: every analyte of the run.")
    parser.add_argument("--instrument", default="", help="Instrument, when the run exists on more than one.")
    parser.add_argument("--cause", default="", help="What was found to be the cause (write that it is unknown if so).")
    parser.add_argument("--action", default="", help="What was done.")
    parser.add_argument("--outcome", default="", help="What followed, e.g. the repeat control result.")
    parser.add_argument("--patient-impact", default="", help="Effect on patient results: held, re-tested, none.")
    parser.add_argument("--by", default="", help="Who records this.")
    parser.add_argument("--supersedes", type=int, default=0, metavar="ID", help="Correct an earlier action record: this record replaces it.")
    _dialog_arguments(parser, "the record (which rejected run, cause, action, who)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of human text.")


@register_command(
    "qc-action",
    help="Record what was found and done about a rejected QC run (kept in the project file).",
    description=(
        "Add a corrective-action record to a sync project, or list the rejected runs that have none.\n"
        "The record goes into the project .tame (not the source data, not the recomputed result), is chained\n"
        "by hash and logged, and appears in the QC report. Records are never edited: correct one with --supersedes."
    ),
    epilog=(
        "Examples:\n"
        "  tametools qc-action qc.tame --list\n"
        "  tametools qc-action qc.tame --dialog        (choose the rejected run and fill in the record in a window)\n"
        "  tametools qc-action qc.tame --run 2026-11-01 --result Glucose --cause \"...\" --action \"...\" --by NAME\n"
        "  tametools qc-action qc.tame --supersedes 3 --cause \"...\" --action \"...\" --by NAME"
    ),
    configure=_action_arguments,
)
def qc_action(args) -> int:
    if args.list:
        status = action_status(args.path)
        if args.json:
            _print_json({"open": status["open"], "rejected_runs": len(status["rejected"]), "records": status["records"],
                         "records_not_matching_their_hash": status["broken"]})
            return 0
        print(f"조치 기록이 없는 거부 run {len(status['open'])}건 (전체 거부 run {len(status['rejected'])}건)")
        for entry in status["open"]:
            print(f"  {entry['run']}  {entry['name']}  수준 {', '.join(entry['levels'])}  {', '.join(entry['rules'])}")
        actions = [record for record in status["records"] if record.get("KIND") == "ACTION"]
        in_force = {record.get("ID") for record in effective(status["records"], "ACTION")}
        print(f"조치 기록 {len(actions)}건" + (f" (정정으로 대체된 기록 {len(actions) - len(in_force)}건 포함)" if len(actions) != len(in_force) else ""))
        for record in actions:
            replaced = "" if record.get("ID") in in_force else "  [대체됨]"
            print(f"  #{record.get('ID')}  {str(record.get('AT'))[:16].replace('T', ' ')}  {record.get('BY')}  run {record.get('RUN')}  "
                  f"{', '.join(record.get('RESULTS') or []) or '전체 검사'}  조치: {record.get('ACTION')}{replaced}")
        if status["broken"]:
            print("주의: 도구 밖에서 수정되었거나 빠진 기록이 있습니다: #" + ", #".join(str(item) for item in status["broken"]))
        return 0
    results = [part.strip() for item in args.result for part in item.split(",") if part.strip()]
    given = {"run": args.run or "", "results": results, "instrument": args.instrument, "cause": args.cause, "action": args.action,
             "outcome": args.outcome, "patient_impact": args.patient_impact, "by": args.by, "supersedes": args.supersedes}
    complete = bool((args.run or args.supersedes) and args.cause.strip() and args.action.strip() and args.by.strip())
    if _ask(args, complete=complete):
        try:
            given = ask_action(args.path, **given)
        except NothingToRecord as exc:
            print(exc)
            return 0
        if given is None:
            print("취소했습니다. 기록을 남기지 않았습니다.")
            return 1
    outcome = add_action(args.path, **given)
    record = outcome["record"]
    if args.json:
        _print_json({"record": record, "report": outcome["report"], "open": outcome["open"]})
        return 0
    print(f"조치 기록 #{record['ID']} 을(를) 남겼습니다: run {record['RUN']} · {', '.join(record['RESULTS']) or '전체 검사'} · {record['BY']}"
          + (f" (#{record['SUPERSEDES']} 정정)" if record["SUPERSEDES"] else ""))
    if not outcome["rejected_in_target"]:
        print("  참고: 이 run 은 지금 거부 상태가 아닙니다. 기록은 그대로 남습니다.")
    print("  보고서: " + _REPORT[outcome["report"]])
    print(f"  아직 조치 기록이 없는 거부 run: {len(outcome['open'])}건")
    return 0


def _review_arguments(parser) -> None:
    parser.add_argument("path", metavar="PROJECT", help="Project .tame file with a META[SYNC] section.")
    parser.add_argument("--list", action="store_true", help="List the review records.")
    parser.add_argument("--from", dest="start", default="", metavar="DATE", help="First day reviewed (YYYY-MM-DD).")
    parser.add_argument("--to", dest="end", default="", metavar="DATE", help="Last day reviewed (YYYY-MM-DD).")
    parser.add_argument("--by", default="", help="Reviewer.")
    parser.add_argument("--note", default="", help="Optional remark.")
    parser.add_argument("--supersedes", type=int, default=0, metavar="ID", help="Correct an earlier review record.")
    _dialog_arguments(parser, "the record (period, reviewer)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of human text.")


@register_command(
    "qc-review",
    help="Record that the QC of a period was reviewed (kept in the project file).",
    description="Add a review record for a period (both days included), or list the review records. Shown in the QC report.",
    epilog="Examples:\n  tametools qc-review qc.tame --from 2026-10-01 --to 2026-10-31 --by NAME\n  tametools qc-review qc.tame --list",
    configure=_review_arguments,
)
def qc_review(args) -> int:
    if args.list:
        records = sync_records(read_tame(args.path))
        reviews = [record for record in records if record.get("KIND") == "REVIEW"]
        if args.json:
            _print_json({"records": reviews, "records_not_matching_their_hash": verify_records(records)})
            return 0
        in_force = {record.get("ID") for record in effective(records, "REVIEW")}
        print(f"검토 기록 {len(reviews)}건")
        for record in reviews:
            replaced = "" if record.get("ID") in in_force else "  [대체됨]"
            print(f"  #{record.get('ID')}  {record.get('FROM')} ~ {record.get('TO')}  {record.get('BY')}  {str(record.get('AT'))[:16].replace('T', ' ')}  "
                  f"run {record.get('RUNS')} · 거부 {record.get('REJECTED_RUNS')} · 당시 미조치 {record.get('OPEN_AT_REVIEW')}{replaced}")
        return 0
    given = {"start": args.start, "end": args.end, "by": args.by, "note": args.note, "supersedes": args.supersedes}
    complete = bool(args.start.strip() and args.end.strip() and args.by.strip())
    if _ask(args, complete=complete):
        given = ask_review(args.path, **given)
        if given is None:
            print("취소했습니다. 기록을 남기지 않았습니다.")
            return 1
    outcome = add_review(args.path, **given)
    record = outcome["record"]
    if args.json:
        _print_json({"record": record, "report": outcome["report"], "open_in_period": outcome["open_in_period"]})
        return 0
    print(f"검토 기록 #{record['ID']} 을(를) 남겼습니다: {record['FROM']} ~ {record['TO']} · {record['BY']}")
    print(f"  기간 내 run {record['RUNS']}개, 거부 run {record['REJECTED_RUNS']}건, 조치 기록이 없는 거부 run {record['OPEN_AT_REVIEW']}건")
    if outcome["open_in_period"]:
        print("  주의: 조치 기록 없이 검토를 남겼습니다. 목록: tametools qc-action PROJECT --list")
    print("  보고서: " + _REPORT[outcome["report"]])
    return 0

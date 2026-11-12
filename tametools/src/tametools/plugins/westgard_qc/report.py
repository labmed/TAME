"""Self-contained HTML report for Westgard QC results, with Levey-Jennings charts.

One file, no external assets and no optional dependencies: the charts are inline
SVG. The report is written for the person who reviews the control runs:

1. what the last sync added and how it was judged,
2. where each analyte and control level stands (statistics against the declared baseline),
3. one Levey-Jennings chart per control material,
4. every warning and rejection in the period, with room to record the action taken,
5. the rules and baselines that were applied.

It supports manual review; it does not release or hold patient results.
"""
from __future__ import annotations

from html import escape
import json
import math
from typing import Any, Mapping

import pandas as pd

from .actions import ACTION, REVIEW, covers, effective, open_rejections, rejected_runs, review_covers
from .multirule import IN_CONTROL, NOT_EVALUATED, REJECT, WARNING, RulePlan, parse_rule

DEFAULT_LAST_RUNS = 30
_Z_RANGE = 4.0  # the chart shows mean +/- 4 SD; values beyond are drawn at the edge and labelled

_DECISION_LABEL = {REJECT: "거부", WARNING: "경고", IN_CONTROL: "관리 내", NOT_EVALUATED: "미평가"}
_DECISION_CLASS = {REJECT: "reject", WARNING: "warning", IN_CONTROL: "ok", NOT_EVALUATED: "none"}
_SCOPE_LABEL = {"point": "", "across_run": "같은 물질의 연속 run", "within_run": "같은 run 의 여러 수준",
                "across_run_material": "여러 수준을 합친 연속 run"}
_SCOPE_SETTING = {"BOTH": "물질 내 연속 + 수준 간", "WITHIN_MATERIAL": "같은 물질의 연속 run 만", "ACROSS_MATERIAL": "수준 간(같은 run·연속 run)만"}
_AFTER_REJECT_LABEL = {"RESTART": "그 검사의 연속 계수를 처음부터 다시 시작(RESTART)",
                       "SKIP": "거부된 run 의 값만 빼고 이어서 계수(SKIP)", "CONTINUE": "거부된 run 의 값도 계속 계수(CONTINUE)"}
_ERROR_LABEL = {"random": "우연오차", "systematic": "계통오차", "random+systematic": "우연+계통오차"}

_CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--series:#2a78d6;--good:#0ca30c;--warning:#fab219;--critical:#d03b3b;--border:rgba(11,11,11,.10);--wash:rgba(42,120,214,.07)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--series:#3987e5;--border:rgba(255,255,255,.10);--wash:rgba(57,135,229,.10)}}
@media print{:root{color-scheme:light;--page:#fff;--surface:#fff;--ink:#0b0b0b;--ink2:#52514e;--grid:#e1e0d9;--axis:#c3c2b7;--series:#2a78d6;--border:rgba(11,11,11,.18)}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.6 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 20px 48px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:34px 0 10px}h3{font-size:15px;margin:0 0 2px}
p{margin:6px 0}.sub{color:var(--ink2)}.small{font-size:12.5px;color:var(--ink2)}
.meta{display:flex;flex-wrap:wrap;gap:4px 22px;color:var(--ink2);font-size:13px;margin:8px 0 0}
.meta b{color:var(--ink);font-weight:600}
.notice{border-left:3px solid var(--axis);padding:6px 12px;margin:14px 0;color:var(--ink2);font-size:13px}
.notice.alert{border-left-color:var(--critical)}.notice b{color:var(--ink)}
.act{margin:0 0 6px}.act span{color:var(--ink2);margin-right:4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px}
.tile .label{color:var(--ink2);font-size:12.5px}.tile .value{font-size:26px;font-weight:600;line-height:1.25}
.tile .note{color:var(--ink2);font-size:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin:12px 0;break-inside:avoid}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600;white-space:nowrap}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}td.nw{white-space:nowrap}
tr.flag-reject td{background:rgba(208,59,59,.06)}
.chip{white-space:nowrap}.chip i{display:inline-block;width:1.1em;font-style:normal;font-weight:700;text-align:center}
.chip.reject i{color:var(--critical)}.chip.warning i{color:#b97f00}.chip.ok i{color:var(--good)}.chip.none i{color:var(--muted)}
.change{color:var(--ink2)}.change b{color:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:12.5px;color:var(--ink2);margin:4px 0 8px}
.legend svg{vertical-align:-3px;margin-right:4px}
.panel-head{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;color:var(--ink2);margin-top:10px}
.panel-head b{color:var(--ink);font-weight:600}
svg.lj{display:block;width:100%;height:auto;overflow:visible}
svg.lj text{font:11px system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif;fill:var(--muted)}
svg.lj text.flag{fill:var(--ink2);font-weight:600}
svg.lj .grid{stroke:var(--grid);stroke-width:1}.lj .limit2{stroke:var(--axis);stroke-width:1}.lj .limit3{stroke:var(--muted);stroke-width:1}
.lj .mean{stroke:var(--axis);stroke-width:1.5}.lj .band{fill:var(--wash)}
.lj .line{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.lj .dot{fill:var(--series);stroke:var(--surface);stroke-width:2}
.lj .warn{fill:var(--warning);stroke:var(--surface);stroke-width:2}.lj .rej{fill:var(--critical);stroke:var(--surface);stroke-width:2}
.lj .missing{stroke:var(--muted);stroke-width:1.5;fill:none}.lj .new{fill:none;stroke:var(--ink);stroke-width:1.5}
.lj .lot{stroke:var(--muted);stroke-width:1}.lj .hit{fill:transparent;cursor:default}
.lj g.pt:hover .dot,.lj g.pt:hover .warn,.lj g.pt:hover .rej{stroke:var(--ink)}
#tip{position:fixed;display:none;pointer-events:none;z-index:9;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:6px;padding:7px 10px;font-size:12.5px;line-height:1.45;box-shadow:0 2px 10px rgba(0,0,0,.14);max-width:280px}
#tip div{white-space:nowrap}#tip .v{font-weight:600;font-size:14px}#tip .k{color:var(--ink2)}
.sign{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:10px}
.sign div{border-bottom:1px solid var(--axis);padding:22px 0 4px;color:var(--ink2);font-size:12.5px}
details{margin:8px 0}summary{cursor:pointer;color:var(--ink2)}
@page{size:A4 landscape;margin:11mm}
@media print{body{font-size:12px}main{max-width:none;padding:0}.card,.tile{border-color:#bbb}#tip{display:none!important}h2{break-after:avoid}h2.charts{break-before:page}.scroll{overflow:visible}table{font-size:11px}th,td{padding:4px 6px}th{white-space:normal}tr{break-inside:avoid}.tiles{grid-template-columns:repeat(5,1fr)}}
"""

_SCRIPT = """
(function(){var tip=document.getElementById('tip');if(!tip)return;
function row(k,v,strong){var d=document.createElement('div');if(k){var s=document.createElement('span');s.className='k';s.textContent=k+' ';d.appendChild(s);}
var b=document.createElement('span');if(strong)b.className='v';b.textContent=v;d.appendChild(b);return d;}
function show(e){var p=JSON.parse(this.getAttribute('data-p'));tip.textContent='';
tip.appendChild(row('',p.value,true));tip.appendChild(row('',p.when));tip.appendChild(row('z',p.z));
tip.appendChild(row('판정',p.decision));if(p.rules)tip.appendChild(row('규칙',p.rules));if(p.isNew)tip.appendChild(row('','이번 동기화에서 추가·변경'));
tip.style.display='block';move(e);}
function move(e){var x=e.clientX+14,y=e.clientY+14,w=tip.offsetWidth,h=tip.offsetHeight;
if(x+w>window.innerWidth-8)x=e.clientX-w-14;if(y+h>window.innerHeight-8)y=e.clientY-h-14;tip.style.left=x+'px';tip.style.top=y+'px';}
function hide(){tip.style.display='none';}
document.querySelectorAll('g.pt').forEach(function(g){g.addEventListener('pointerenter',show);g.addEventListener('pointermove',move);g.addEventListener('pointerleave',hide);});
})();
"""


# --------------------------------------------------------------------------- small helpers

def _text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def _number(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if not math.isfinite(number) else format(number, f".{digits}g")


def _decimals(sd: Any) -> int:
    """Decimal places that show the declared SD as written (0.03 -> 2, 1.5 -> 1, 2 -> 0)."""
    try:
        text = format(float(sd), ".3g")
    except (TypeError, ValueError):
        return 2
    if "e" in text:
        return 4
    return len(text.split(".")[1]) if "." in text else 0


def _fixed(value: Any, decimals: int) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if not math.isfinite(number) else f"{number:.{max(decimals, 0)}f}"


def _signed(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if not math.isfinite(number) else f"{number:+.2f}"


def _when(stamp: Any) -> str:
    text = _text(stamp)
    return text[:16].replace("T", " ") if len(text) >= 16 else text


def _chip(decision: str) -> str:
    icon = {REJECT: "✕", WARNING: "▲", IN_CONTROL: "●", NOT_EVALUATED: "–"}.get(decision, "?")
    return (f"<span class='chip {_DECISION_CLASS.get(decision, 'none')}'><i aria-hidden='true'>{icon}</i>"
            f"{escape(_DECISION_LABEL.get(decision, _text(decision)))}</span>")


def _rules_text(row: Mapping) -> str:
    """Rule names for a row, rejection rules first; '' when nothing fired."""
    names = [name for column in ("violation", "warning") for name in _text(row.get(column)).split(", ") if name and name not in {"NONE", "NE"}]
    return ", ".join(dict.fromkeys(names))


def _scope_text(value: Any) -> str:
    parts = []
    for item in _text(value).split("; "):
        if ":" not in item:
            continue
        name, where = item.split(":", 1)
        labels = [_SCOPE_LABEL.get(part, part) for part in where.split("+")]
        labels = [label for label in labels if label]
        if labels:
            parts.append(f"{name}: " + " + ".join(labels))
    return "; ".join(parts)


_CHANGE_LABEL = {"value": "값", "violation": "거부 규칙", "warning": "경고 규칙", "rule_scope": "걸린 방식",
                 "decision": "측정 판정", "run_decision": "run 판정"}


def _change_value(column: str, value: Any) -> str:
    text = _text(value)
    if column in {"decision", "run_decision"}:
        return _DECISION_LABEL.get(text, text)
    if column == "rule_scope":
        return _scope_text(text) or "없음"
    if text in {"NONE", "NE", "<ABSENT>"}:
        return "없음"
    return _number(text) if column == "value" else text


def _table(headers: list[tuple[str, str]], rows: list[list[str]], *, row_classes: list[str] | None = None) -> str:
    """headers: (label, 'num' or ''); cells are already HTML."""
    head = "".join(f"<th class='{kind}'>{escape(label)}</th>" for label, kind in headers)
    body = []
    for index, cells in enumerate(rows):
        css = f" class='{row_classes[index]}'" if row_classes and row_classes[index] else ""
        body.append(f"<tr{css}>" + "".join(f"<td class='{kind}'>{cell}</td>" for cell, (_, kind) in zip(cells, headers)) + "</tr>")
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


# --------------------------------------------------------------------------- data preparation

def _prepare(result: pd.DataFrame) -> pd.DataFrame:
    frame = result.copy()
    for column in ("value", "mean", "sd", "z"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["_moment"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    if "sync_status" not in frame.columns:
        frame["sync_status"] = ""
    for column in ("result_id", "instrument", "lot", "level", "run", "unit", "decision", "run_decision"):
        frame[column] = frame[column].map(_text)
    # Show the analyte as the laboratory names it (the source column), not the internal id.
    names = frame["source_result_column"].map(_text) if "source_result_column" in frame.columns else frame["result_id"]
    frame["_name"] = names.where(names.ne(""), frame["result_id"])
    return frame.sort_values(["_moment", "run", "result_id", "level", "lot"], kind="stable").reset_index(drop=True)


def _window(frame: pd.DataFrame, last_runs: int) -> pd.DataFrame:
    """The last ``last_runs`` runs of every analyte on every instrument."""
    kept = []
    for _, group in frame.groupby(["result_id", "instrument"], sort=False):
        order = group.groupby("run", sort=False)["_moment"].min().sort_values(kind="stable")
        runs = list(order.index[-last_runs:])
        part = group.loc[group["run"].isin(runs)].copy()
        part["_run_index"] = part["run"].map({run: position for position, run in enumerate(runs)})
        part["_run_count"] = len(runs)
        kept.append(part)
    return pd.concat(kept) if kept else frame.assign(_run_index=0, _run_count=0)


# --------------------------------------------------------------------------- Levey-Jennings chart (inline SVG)

_W, _LEFT, _RIGHT, _TOP, _PLOT_H, _BOTTOM = 940, 62, 50, 16, 168, 26


def _marker(kind: str, x: float, y: float) -> str:
    if kind == "rej":   # diamond
        return f"<path class='rej' d='M{x:.1f},{y - 6.5:.1f} L{x + 6.5:.1f},{y:.1f} L{x:.1f},{y + 6.5:.1f} L{x - 6.5:.1f},{y:.1f} Z'/>"
    if kind == "warn":  # triangle
        return f"<path class='warn' d='M{x:.1f},{y - 6.5:.1f} L{x + 6.5:.1f},{y + 5:.1f} L{x - 6.5:.1f},{y + 5:.1f} Z'/>"
    if kind == "missing":
        return f"<path class='missing' d='M{x - 4:.1f},{y - 4:.1f} L{x + 4:.1f},{y + 4:.1f} M{x - 4:.1f},{y + 4:.1f} L{x + 4:.1f},{y - 4:.1f}'/>"
    return f"<circle class='dot' cx='{x:.1f}' cy='{y:.1f}' r='4.5'/>"


def _legend() -> str:
    def swatch(inner: str) -> str:
        return f"<svg class='lj' width='18' height='16' viewBox='0 0 18 16' style='display:inline;width:18px'>{inner}</svg>"
    ring = "<circle class='new' cx='9' cy='8' r='7'/>" + _marker("dot", 9, 8)
    return ("<div class='legend'>"
            f"<span>{swatch(_marker('dot', 9, 8))}관리 내</span>"
            f"<span>{swatch(_marker('warn', 9, 8.5))}경고 규칙</span>"
            f"<span>{swatch(_marker('rej', 9, 8))}거부 규칙</span>"
            f"<span>{swatch(_marker('missing', 9, 8))}결과 없음</span>"
            f"<span>{swatch(ring)}이번 동기화에서 추가·변경</span>"
            "<span>가로선: 평균, ±1SD, ±2SD, ±3SD (엷은 띠 = ±2SD 이내)</span></div>")


def lj_chart_svg(material: pd.DataFrame, run_count: int, run_times: Mapping[int, str] | None = None) -> str:
    """One control material (level + lot + baseline) over the report window.

    ``run_times`` maps every run position of the analyte to its time, so the
    date axis is the same for all levels even when a level skipped a run.
    """
    mean, sd = float(material["mean"].iloc[0]), float(material["sd"].iloc[0])
    unit = _text(material["unit"].iloc[0])
    plot_w = _W - _LEFT - _RIGHT
    height = _TOP + _PLOT_H + _BOTTOM
    step = plot_w / max(run_count, 1)

    def x_of(index: float) -> float:
        return _LEFT + (index + 0.5) * step

    def y_of(z: float) -> float:
        return _TOP + (_Z_RANGE - max(-_Z_RANGE, min(_Z_RANGE, z))) / (2 * _Z_RANGE) * _PLOT_H

    out = [f"<svg class='lj' viewBox='0 0 {_W} {height}' role='img' aria-label='Levey-Jennings chart'>"]
    out.append(f"<rect class='band' x='{_LEFT}' y='{y_of(2):.1f}' width='{plot_w}' height='{y_of(-2) - y_of(2):.1f}'/>")
    for k in (-3, -2, -1, 0, 1, 2, 3):
        y = y_of(k)
        css = "mean" if k == 0 else "limit3" if abs(k) == 3 else "limit2" if abs(k) == 2 else "grid"
        out.append(f"<line class='{css}' x1='{_LEFT}' x2='{_LEFT + plot_w}' y1='{y:.1f}' y2='{y:.1f}'/>")
        out.append(f"<text x='{_LEFT - 8}' y='{y + 3.5:.1f}' text-anchor='end'>{escape(_fixed(mean + k * sd, _decimals(sd)))}</text>")
        out.append(f"<text x='{_LEFT + plot_w + 8}' y='{y + 3.5:.1f}'>{'평균' if k == 0 else f'{k:+d}SD'}</text>")
    out.append(f"<text x='{_LEFT - 8}' y='{_TOP - 4}' text-anchor='end'>{escape(unit)}</text>")

    # x axis: one position per run of the analyte, labelled with the run date.
    positions = dict(run_times) if run_times else material.drop_duplicates("_run_index").set_index("_run_index")["time"].to_dict()
    every = max(1, math.ceil(run_count / 12))
    for index in range(run_count):
        if index % every == 0 or index == run_count - 1 and (index % every) > every / 2:
            stamp = positions.get(index)
            if stamp:
                out.append(f"<text x='{x_of(index):.1f}' y='{_TOP + _PLOT_H + 16}' text-anchor='middle'>{escape(_text(stamp)[5:10])}</text>")

    rows = material.sort_values("_run_index").to_dict(orient="records")
    # The line connects consecutive evaluated runs; a missing result or a skipped run breaks it.
    segment: list[str] = []
    previous = None
    for row in rows:
        if math.isnan(row["z"]) or (previous is not None and row["_run_index"] != previous + 1):
            if len(segment) > 1:
                out.append(f"<polyline class='line' points='{' '.join(segment)}'/>")
            segment = []
        if not math.isnan(row["z"]):
            segment.append(f"{x_of(row['_run_index']):.1f},{y_of(row['z']):.1f}")
        previous = row["_run_index"]
    if len(segment) > 1:
        out.append(f"<polyline class='line' points='{' '.join(segment)}'/>")

    label_slots: list[tuple[float, float]] = []
    for row in rows:
        x = x_of(row["_run_index"])
        missing = math.isnan(row["z"])
        y = _TOP + _PLOT_H - 7 if missing else y_of(row["z"])
        kind = "missing" if missing else "rej" if row["decision"] == REJECT else "warn" if row["decision"] == WARNING else "dot"
        rules = _rules_text(row)
        is_new = row["sync_status"] in {"NEW", "CHANGED"}
        payload = {"value": ("결과 없음" if missing else f"{_number(row['value'])} {unit}".strip()), "when": _when(row["time"]),
                   "z": _signed(row["z"]), "decision": _DECISION_LABEL.get(row["decision"], row["decision"]),
                   "rules": rules, "isNew": is_new}
        title = " · ".join(part for part in (payload["when"], payload["value"], "z " + payload["z"], payload["decision"], rules) if part)
        out.append(f"<g class='pt' data-p='{escape(json.dumps(payload, ensure_ascii=False), quote=True)}'><title>{escape(title)}</title>")
        if is_new:
            out.append(f"<circle class='new' cx='{x:.1f}' cy='{y:.1f}' r='9.5'/>")
        out.append(_marker(kind, x, y))
        out.append(f"<circle class='hit' cx='{x:.1f}' cy='{y:.1f}' r='12'/></g>")
        if kind in {"rej", "warn"}:
            beyond = abs(row["z"]) > _Z_RANGE
            # On the chart a rejected point names its rejection rules only; the table lists everything.
            shown = _text(row.get("violation")) if kind == "rej" else _text(row.get("warning"))
            text = shown + (f" ({_signed(row['z'])})" if beyond else "")
            above = row["z"] >= 0 and not (beyond and row["z"] > 0)
            label_y = y - 11 if above else y + 19
            while any(abs(label_x - x) < 9 + 3.2 * len(text) and abs(other_y - label_y) < 11 for label_x, other_y in label_slots):
                label_y += -11 if above else 11
            label_slots.append((x, label_y))
            anchor = "end" if x > _LEFT + plot_w - 40 else "start" if x < _LEFT + 40 else "middle"
            out.append(f"<text class='flag' x='{x:.1f}' y='{label_y:.1f}' text-anchor='{anchor}'>{escape(text)}</text>")
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- sections

def _material_statistics(window: pd.DataFrame) -> pd.DataFrame:
    rows = []
    keys = ["result_id", "instrument", "level", "lot", "baseline_key"]
    for key, group in window.groupby(keys, sort=False):
        accepted = group.loc[group["run_decision"].ne(REJECT), "value"]
        values = accepted.dropna()
        excluded = int(group.loc[group["run_decision"].eq(REJECT), "value"].notna().sum())
        mean, sd = float(group["mean"].iloc[0]), float(group["sd"].iloc[0])
        observed_mean = float(values.mean()) if len(values) else math.nan
        observed_sd = float(values.std(ddof=1)) if len(values) > 1 else math.nan
        latest = group.sort_values("_moment").iloc[-1]
        rows.append({**dict(zip(keys, key)), "name": group["_name"].iloc[0], "unit": group["unit"].iloc[0], "mean": mean, "sd": sd,
                     "target_cv": 100 * sd / abs(mean) if mean else math.nan,
                     "n": int(len(values)), "missing": int(group["value"].isna().sum()), "excluded": excluded,
                     "sd_ratio": observed_sd / sd if sd else math.nan,
                     "observed_mean": observed_mean, "observed_sd": observed_sd,
                     "observed_cv": 100 * observed_sd / abs(observed_mean) if observed_mean else math.nan,
                     "bias_sd": (observed_mean - mean) / sd if sd else math.nan,
                     "warnings": int(group["decision"].eq(WARNING).sum()), "rejects": int(group["decision"].eq(REJECT).sum()),
                     "latest": latest["decision"], "latest_run": latest["run_decision"], "latest_time": latest["time"],
                     "from": group["_moment"].min(), "to": group["_moment"].max()})
    return pd.DataFrame(rows)


def _plans(settings: Mapping, identifiers: list[str]) -> dict[str, RulePlan]:
    rulesets = settings.get("RULESETS") if isinstance(settings.get("RULESETS"), Mapping) else {}
    plans = {}
    for identifier in identifiers:
        item = rulesets.get(identifier) or settings
        plans[identifier] = RulePlan(rules=tuple(item.get("RULES", [])), warning_rules=tuple(item.get("WARNING_RULES", [])),
                                     scope=str(item.get("SCOPE", "BOTH")), trigger=str(item.get("TRIGGER", "ALWAYS")),
                                     rule_scopes=dict(item.get("RULE_SCOPES", {}) or {}),
                                     after_reject=str(item.get("AFTER_REJECT", "RESTART")),
                                     rule_forms=dict(item.get("RULE_FORMS", {}) or {}))
    return plans


def _rule_meaning(name: str, form: str = "COUNT") -> str:
    rule = parse_rule(name)
    limit = format(rule.limit, "g")
    if rule.kind == "single":
        return f"측정 1개가 평균 ±{limit}SD 를 벗어남"
    if rule.kind == "range":
        if form == "RANGE":
            return f"한 run 에서 가장 높은 값과 낮은 값의 차이가 {format(2 * rule.limit, 'g')}SD 이상"
        return f"한 run 에서 한 수준은 +{limit}SD 초과, 다른 수준은 −{limit}SD 미만"
    if rule.kind == "count":
        return f"연속 {rule.n}개 중 {rule.m}개가 같은 쪽 {limit}SD 를 벗어남"
    if rule.limit == 0:
        return f"연속 {rule.n}개가 평균의 같은 쪽"
    return f"연속 {rule.n}개가 같은 쪽 {limit}SD 를 벗어남"


def _settings_section(settings: Mapping, window: pd.DataFrame) -> str:
    identifiers = list(dict.fromkeys(window["result_id"]))
    plans = _plans(settings, identifiers)
    names = list(dict.fromkeys(name for plan in plans.values() for name in [*plan.warning_rules, *plan.rules]))
    labels = window.drop_duplicates("result_id").set_index("result_id")["_name"].to_dict()
    headers = [("규칙", "nw"), ("뜻", ""), ("오차 유형", "nw"), *[(labels.get(identifier, identifier), "") for identifier in identifiers]]
    rows = []
    for name in names:
        rule = parse_rule(name)
        forms = {plan.rule_forms.get(name, "COUNT") for plan in plans.values() if name in plan.rules or name in plan.warning_rules}
        cells = [f"<b>{escape(name)}</b>", escape(_rule_meaning(name, "RANGE" if forms == {"RANGE"} else "COUNT")),
                 escape(_ERROR_LABEL.get(rule.error, rule.error))]
        for identifier in identifiers:
            plan = plans[identifier]
            action = "거부" if name in plan.rules else "경고" if name in plan.warning_rules else "—"
            scope = plan.scope_for(name) if action != "—" else ""
            cells.append(escape(action) + (f"<div class='small'>{escape(_SCOPE_SETTING.get(scope, scope))}</div>" if scope else ""))
        rows.append(cells)
    triggers = {plan.trigger for plan in plans.values()}
    trigger = ("거부 규칙은 경고 여부와 관계없이 항상 적용" if triggers == {"ALWAYS"} else
               "그 run 에 ±2 SD 를 넘은 관리물질이 있을 때만 규칙 적용" if triggers == {"1_2S"} else "검사마다 다름(결과 파일의 설정 참조)")
    policies = {plan.after_reject for plan in plans.values()}
    after = (_AFTER_REJECT_LABEL.get(next(iter(policies)), "") if len(policies) == 1 else "검사마다 다름(결과 파일의 설정 참조)")
    baseline_rows = []
    for _, item in window.drop_duplicates(["result_id", "instrument", "level", "lot", "baseline_key"]).iterrows():
        places = _decimals(item["sd"])
        baseline_rows.append([escape(item["_name"]), escape(item["instrument"]), escape(item["level"]), escape(item["lot"]),
                              _fixed(item["mean"], places), _fixed(item["sd"], places),
                              _fixed(100 * item["sd"] / abs(item["mean"]) if item["mean"] else math.nan, 1), escape(item["unit"]),
                              escape(_text(item.get("baseline_valid_from"))[:10] + " ~ " + _text(item.get("baseline_valid_to"))[:10]),
                              escape(_text(item.get("baseline_reference")))])
    return ("<h2>5. 적용한 규칙과 기준값</h2><div class='card'><h3>검사별 규칙</h3>"
            + _table(headers, rows)
            + f"<p class='small'>적용 방식: {escape(trigger)}. 거부된 run 이후: {escape(after)}. "
              "결측 결과와 lot·기준값 변경도 연속 판정을 끊습니다. 한계값과 정확히 같은 값은 벗어난 것으로 보지 않고, "
              "평균과 정확히 같은 값은 '평균의 같은 쪽' 연속을 끊습니다(이 도구의 기준).</p></div>"
            + "<div class='card'><h3>기준값(선언값)</h3>"
            + _table([("검사", ""), ("장비", ""), ("수준", ""), ("lot", ""), ("평균", "num"), ("SD", "num"), ("CV%", "num"), ("단위", ""),
                      ("유효기간", ""), ("근거", "")], baseline_rows)
            + "<p class='small'>판정은 이 자료에서 다시 계산한 평균·SD 가 아니라 위 선언값을 기준으로 합니다.</p></div>")


def _sync_section(sync: Mapping, frame: pd.DataFrame) -> str:
    data, result = sync.get("data", {}), sync.get("result", {})
    touched = frame.loc[frame["sync_status"].isin(["NEW", "CHANGED"])]
    new = frame.loc[frame["sync_status"].eq("NEW")]
    everything_new = len(new) == len(frame) and len(frame) > 0
    added, modified, removed = (data.get(name + "_count", len(data.get(name, []))) for name in ("added", "modified", "removed"))
    tiles = [("원본에 추가된 행", added, f"수정 {modified} · 삭제 {removed}"),
             ("새 결과", result.get("new", len(new)), "측정 × 검사"),
             ("새 결과 중 거부", int(new["decision"].eq(REJECT).sum()), "거부 규칙 위반"),
             ("새 결과 중 경고", int(new["decision"].eq(WARNING).sum()), "경고 규칙만"),
             ("달라진 기존 결과", result.get("changed", 0), "이전 보고 대비")]
    html = ["<h2>1. 이번 동기화</h2><div class='tiles'>"]
    for label, value, note in tiles:
        html.append(f"<div class='tile'><div class='label'>{escape(label)}</div><div class='value'>{escape(str(value))}</div>"
                    f"<div class='note'>{escape(note)}</div></div>")
    html.append("</div>")
    for item in data.get("modified", []):
        changes = "; ".join(f"{change['column']}: {change['old']} → {change['new']}" for change in item.get("changes", []))
        html.append(f"<p class='notice'>원본 수정(확인 후 반영): {escape(item.get('key', ''))} — {escape(changes)}</p>")
    for item in data.get("removed", []):
        html.append(f"<p class='notice'>원본에서 삭제(확인 후 반영): {escape(item.get('key', ''))}</p>")
    for reason in sync.get("reasons", []):
        html.append(f"<p class='notice'>{escape(reason)}</p>")
    for warning in sync.get("warnings", []):
        html.append(f"<p class='notice'>주의: {escape(warning)}</p>")
    if everything_new:
        html.append(f"<p class='sub'>처음 만든 결과입니다. 전체 {len(frame)}개 결과가 새로 생겼습니다(아래 3–4절 참조).</p>")
    elif len(touched):
        details = {item["key"]: item["changes"] for item in result.get("changed_detail", [])}
        rows, classes = [], []
        for _, item in touched.sort_values(["_moment", "result_id", "level"]).iterrows():
            key = " | ".join(f"{name}={item[name]}" for name in result.get("key", []) if name in item)
            changes = details.get(key, [])
            change_text = "".join(f"<div class='change'>{escape(_CHANGE_LABEL[change['column']])}: {escape(_change_value(change['column'], change['old']))}"
                                  f" → <b>{escape(_change_value(change['column'], change['new']))}</b></div>"
                                  for change in changes if change["column"] in _CHANGE_LABEL)
            rows.append(["새 결과" if item["sync_status"] == "NEW" else "변경", escape(_when(item["time"])), escape(item["_name"]),
                         escape(item["level"]), escape(item["lot"]), f"{_number(item['value'])} {escape(item['unit'])}", _signed(item["z"]),
                         escape(_rules_text(item)) or "—", _chip(item["decision"]), _chip(item["run_decision"]), change_text or "—"])
            classes.append("flag-reject" if item["decision"] == REJECT else "")
        html.append("<div class='card'>" + _table([("구분", "nw"), ("측정일시", "nw"), ("검사", ""), ("수준", ""), ("lot", ""), ("값", "num"), ("z", "num"),
                                                 ("규칙", ""), ("측정 판정", "nw"), ("run 판정", "nw"), ("이전 보고와 달라진 점", "")], rows, row_classes=classes) + "</div>")
    else:
        html.append("<p class='sub'>새로 생기거나 달라진 결과가 없습니다.</p>")
    return "".join(html)


def _status_section(statistics: pd.DataFrame, last_runs: int) -> str:
    rows, classes = [], []
    for _, item in statistics.iterrows():
        places = _decimals(item["sd"])
        rows.append([escape(item["name"]), escape(item["level"]), escape(item["lot"]),
                     f"{_fixed(item['mean'], places)} ± {_fixed(item['sd'], places)} {escape(item['unit'])}", _fixed(item["target_cv"], 1),
                     str(item["n"]) + "".join(f"<div class='small'>{label} {number}</div>" for label, number in
                                              (("거부 run 제외", item["excluded"]), ("결측", item["missing"])) if number),
                     _fixed(item["observed_mean"], places + 1), _fixed(item["observed_sd"], places + 2), _fixed(item["observed_cv"], 1), _signed(item["bias_sd"]), _fixed(item["sd_ratio"], 2),
                     str(item["warnings"]), str(item["rejects"]), _chip(item["latest_run"]) + f"<div class='small'>{escape(_when(item['latest_time']))}</div>"])
        classes.append("flag-reject" if item["latest_run"] == REJECT else "")
    return ("<h2>2. 검사·수준별 현황</h2><div class='card'>"
            + _table([("검사", ""), ("수준", ""), ("lot", ""), ("기준 평균 ± SD", "nw"), ("기준 CV%", "num"), ("n", "num"), ("관측 평균", "num"),
                      ("관측 SD", "num"), ("관측 CV%", "num"), ("평균 편차(SD)", "num"), ("SD 비", "num"), ("경고", "num"), ("거부", "num"), ("최근 run 판정", "nw")],
                     rows, row_classes=classes)
            + f"<p class='small'>검사·장비별 최근 {last_runs}개 run 기준. 관측 통계는 거부된 run 의 값을 빼고 계산합니다(값 자체는 기록·관리도에 남습니다). "
              "평균 편차(SD) = (관측 평균 − 기준 평균) ÷ 기준 SD, SD 비 = 관측 SD ÷ 기준 SD.</p>"
            + _limit_notes(statistics) + "</div>")


def _limit_notes(statistics: pd.DataFrame) -> str:
    """Point out declared limits that the observed data do not support (only with enough accepted runs)."""
    notes = []
    for _, item in statistics.iterrows():
        label = f"{item['name']} 수준 {item['level']} (lot {item['lot']})"
        if item["n"] < 20:
            continue
        if item["sd_ratio"] < 0.5:
            notes.append(f"{label}: 관측 SD 가 기준 SD 의 {item['sd_ratio']:.2f}배입니다. 기준 SD 가 넓으면(예: 제조사 허용범위) 규칙이 오차를 잘 잡지 못합니다.")
        elif item["sd_ratio"] > 1.5:
            notes.append(f"{label}: 관측 SD 가 기준 SD 의 {item['sd_ratio']:.2f}배입니다. 기준 SD 가 좁으면 거짓 거부가 늘어납니다.")
        if abs(item["bias_sd"]) > 1:
            notes.append(f"{label}: 관측 평균이 기준 평균에서 {item['bias_sd']:+.2f} SD 떨어져 있습니다. 기준 평균을 다시 확인하십시오.")
    short = sorted({f"{item['name']} 수준 {item['level']}" for _, item in statistics.iterrows() if item["n"] < 20})
    html = "".join(f"<p class='notice'>기준값 점검: {escape(note)}</p>" for note in notes)
    if short:
        html += ("<p class='small'>기준값 점검(SD 비, 평균 편차)은 채택된 측정이 20개 이상일 때만 표시합니다. 20개 미만: "
                 + escape(", ".join(short)) + ".</p>")
    return html


def _chart_section(window: pd.DataFrame) -> str:
    html = ["<h2 class='charts'>3. Levey-Jennings 관리도</h2>", _legend()]
    for (identifier, instrument), group in window.groupby(["result_id", "instrument"], sort=False):
        unit = group["unit"].iloc[0]
        first, last = group["_moment"].min(), group["_moment"].max()
        period = f"{_text(group.loc[group['_moment'].eq(first), 'time'].iloc[0])[:10]} ~ {_text(group.loc[group['_moment'].eq(last), 'time'].iloc[0])[:10]}"
        run_times = group.drop_duplicates("_run_index").set_index("_run_index")["time"].to_dict()
        html.append(f"<div class='card'><h3>{escape(group['_name'].iloc[0])} <span class='sub'>({escape(unit)}) · {escape(instrument)}</span></h3>"
                    f"<div class='small'>{escape(period)} · {int(group['_run_count'].iloc[0])}개 run</div>")
        for (level, lot, _), material in group.groupby(["level", "lot", "baseline_key"], sort=False):
            mean, sd = material["mean"].iloc[0], material["sd"].iloc[0]
            warnings, rejects = int(material["decision"].eq(WARNING).sum()), int(material["decision"].eq(REJECT).sum())
            html.append(f"<div class='panel-head'><span><b>수준 {escape(level)}</b> · lot {escape(lot)} · 기준 {_fixed(mean, _decimals(sd))} ± {_fixed(sd, _decimals(sd))} {escape(unit)}</span>"
                        f"<span>측정 {int(material['value'].notna().sum())} · 경고 {warnings} · 거부 {rejects}</span></div>")
            html.append(lj_chart_svg(material, int(group["_run_count"].iloc[0]), run_times))
        html.append("</div>")
    return "".join(html)


def _action_block(record: Mapping) -> str:
    """One action record as it was written: what was done, why, what followed, and who recorded it."""
    lines = [f"<span>조치</span>{escape(_text(record.get('ACTION')))}", f"<span>원인</span>{escape(_text(record.get('CAUSE')))}"]
    if _text(record.get("OUTCOME")):
        lines.append(f"<span>조치 후</span>{escape(_text(record.get('OUTCOME')))}")
    if _text(record.get("PATIENT_IMPACT")):
        lines.append(f"<span>환자 결과 영향</span>{escape(_text(record.get('PATIENT_IMPACT')))}")
    signature = f"#{record.get('ID')} · {_text(record.get('BY'))} · {_when(record.get('AT'))}"
    if record.get("SUPERSEDES"):
        signature += f" · #{record.get('SUPERSEDES')} 을(를) 정정"
    return "<div class='act'>" + "<br>".join(lines) + f"<div class='small'>{escape(signature)}</div></div>"


def _flag_section(window: pd.DataFrame, records: list[Mapping], standalone: bool = False) -> str:
    actions = effective(records, ACTION)
    flagged = window.loc[window["decision"].isin([REJECT, WARNING, NOT_EVALUATED])].sort_values(["_moment", "result_id", "level"], ascending=[False, True, True])
    shown: set = set()
    rows, classes = [], []
    for _, item in flagged.iterrows():
        scope = _scope_text(item.get("rule_scope"))
        error = _ERROR_LABEL.get(_text(item.get("error_type")), "")
        mine = [record for record in actions if covers(record, instrument=item["instrument"], run=item["run"], result_id=item["result_id"])]
        blocks = [_action_block(record) if record.get("ID") not in shown else f"<div class='small'>#{record.get('ID')} 조치 기록과 같음</div>"
                  for record in mine]
        shown.update(record.get("ID") for record in mine)
        recorded = "".join(blocks) or ("<span class='chip reject'><i aria-hidden='true'>!</i>조치 기록 없음</span>" if item["decision"] == REJECT else "")
        rows.append([escape(_when(item["time"])), escape(item["_name"]), escape(item["level"]), escape(item["lot"]),
                     ("결과 없음" if math.isnan(item["value"]) else f"{_number(item['value'])} {escape(item['unit'])}"), _signed(item["z"]),
                     (escape(_rules_text(item)) or "—") + (f"<div class='small'>{escape(scope)}</div>" if scope else ""),
                     escape(error) or "—", _chip(item["decision"]), recorded])
        classes.append("flag-reject" if item["decision"] == REJECT else "")
    others = [record for record in actions if record.get("ID") not in shown][-10:]
    columns = [("측정일시", "nw"), ("검사", ""), ("수준", ""), ("lot", ""), ("값", "num"), ("z", "num"), ("규칙(걸린 방식)", ""),
               ("오차 유형", "nw"), ("판정", "nw"), ("원인·조치 기록", "")]
    reading = "최근 순. 우연오차 규칙(1_3s, R_4s)은 재현성, 계통오차 규칙(2_2s, 4_1s, 10x)은 치우침(보정·시약·관리물질 변화)을 먼저 의심합니다."
    if standalone:      # a result shown on its own has no project to keep records in
        columns, rows = columns[:-1], [row[:-1] for row in rows]
        html = "<h2>4. 경고·거부 목록</h2>"
    else:
        reading += " 조치 기록은 <b>tametools qc-action</b> 으로 프로젝트 파일에 남기며, 한 번 남긴 기록은 고치지 않고 정정은 새 기록으로 남습니다."
        html = "<h2>4. 경고·거부 목록과 조치 기록</h2>"
    if flagged.empty:
        html += "<p class='sub'>이 기간에 경고나 거부가 없습니다.</p>"
    else:
        html += "<div class='card'>" + _table(columns, rows, row_classes=classes) + f"<p class='small'>{reading}</p></div>"
    if others:
        html += ("<div class='card'><h3>이 목록에 없는 run 의 조치 기록</h3>"
                 + _table([("#", "num"), ("run", "nw"), ("검사", ""), ("기록", "")],
                          [[str(record.get("ID")), escape(_text(record.get("RUN"))), escape(", ".join(record.get("RESULTS") or []) or "전체"),
                            _action_block(record)] for record in reversed(others)])
                 + "<p class='small'>표시 기간 밖이거나 지금은 경고·거부가 아닌 run 에 대한 기록입니다(최근 10건).</p></div>")
    return html


def _open_notice(frame: pd.DataFrame, records: list[Mapping]) -> str:
    """Rejected runs without an action record, over all data (not only the charted window)."""
    rejected = rejected_runs(frame)
    if not rejected:
        return ""
    waiting = open_rejections(frame, records)
    if not waiting:
        return f"<p class='notice'>거부된 run {len(rejected)}건 모두 조치 기록이 있습니다.</p>"
    listed = "; ".join(f"{_when(entry['time'])} {entry['name']} ({', '.join(entry['rules'])})" for entry in waiting[::-1][:8])
    more = f" 외 {len(waiting) - 8}건" if len(waiting) > 8 else ""
    return (f"<p class='notice alert'><b>조치 기록이 없는 거부 run {len(waiting)}건</b> (전체 거부 {len(rejected)}건 중): "
            f"{escape(listed)}{more}</p>")


def _review_section(records: list[Mapping], first_day: str, last_day: str, numbered: bool) -> str:
    reviews = sorted(effective(records, REVIEW), key=lambda record: _text(record.get("AT")), reverse=True)[:10]
    html = f"<h2>{'7. ' if numbered else ''}검토 기록</h2><div class='card'>"
    if reviews:
        html += _table([("#", "num"), ("검토 기간", "nw"), ("검토자", ""), ("기록 시각", "nw"), ("run", "num"), ("거부 run", "num"),
                        ("당시 미조치", "num"), ("메모", "")],
                       [[str(record.get("ID")), escape(f"{_text(record.get('FROM'))} ~ {_text(record.get('TO'))}"), escape(_text(record.get("BY"))),
                         escape(_when(record.get("AT"))), str(record.get("RUNS", "")), str(record.get("REJECTED_RUNS", "")),
                         str(record.get("OPEN_AT_REVIEW", "")), escape(_text(record.get("NOTE"))) or "—"] for record in reviews])
    if review_covers(records, first_day, last_day):
        html += f"<p class='small'>이 보고서의 기간({escape(first_day)} ~ {escape(last_day)})은 위 검토 기록에 포함되어 있습니다.</p>"
    else:
        html += (f"<p class='notice'>이 보고서의 기간({escape(first_day)} ~ {escape(last_day)}) 전체를 포함하는 검토 기록이 아직 없습니다. "
                 "검토 후 <b>tametools qc-review</b> 로 남기거나 아래에 서명하십시오.</p>"
                 "<div class='sign'><div>검토자</div><div>검토일</div><div>확인(책임자)</div></div>")
    return html + "</div>"


def _history_section(history: list[Mapping]) -> str:
    if not history:
        return ""
    rows = [[str(item.get("SEQUENCE", "")), escape(_when(item.get("AT", ""))), str(item.get("ROWS", "")), str(item.get("ADDED", "")),
             str(item.get("MODIFIED", "")), str(item.get("REMOVED", "")), str(item.get("RESULT_NEW", "")), str(item.get("RESULT_CHANGED", "")),
             ("설정 변경 " if item.get("SETTINGS_CHANGED") else "") + escape(_text(item.get("NOTE")))] for item in list(history)[-10:][::-1]]
    return ("<h2>6. 동기화 이력</h2><div class='card'>"
            + _table([("#", "num"), ("시각", ""), ("원본 행", "num"), ("추가", "num"), ("수정", "num"), ("삭제", "num"), ("새 결과", "num"),
                      ("결과 변경", "num"), ("비고", "")], rows)
            + "<p class='small'>최근 10회. 전체 이력과 해시는 프로젝트 파일에 있습니다.</p></div>")


# --------------------------------------------------------------------------- the report

REPORT_OPTIONS = {"TITLE", "LAST_RUNS"}


def report_options(value: Any) -> dict:
    """Validate ``[WESTGARD_QC.REPORT]``: TITLE (text) and LAST_RUNS (runs shown per analyte)."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("WESTGARD_QC.REPORT must be a table with TITLE and/or LAST_RUNS")
    options = {str(key).upper(): item for key, item in value.items()}
    if set(options) - REPORT_OPTIONS:
        raise ValueError("Unknown WESTGARD_QC.REPORT option: " + ", ".join(sorted(set(options) - REPORT_OPTIONS)))
    if "LAST_RUNS" in options:
        runs = options["LAST_RUNS"]
        if isinstance(runs, str) and runs.strip().isdigit():
            runs = int(runs)
        if isinstance(runs, bool) or not isinstance(runs, int) or runs < 2:
            raise ValueError("WESTGARD_QC.REPORT.LAST_RUNS must be an integer >= 2")
        options["LAST_RUNS"] = runs
    if "TITLE" in options and (not isinstance(options["TITLE"], str) or not options["TITLE"].strip()):
        raise ValueError("WESTGARD_QC.REPORT.TITLE must be text")
    return options


def sync_qc_report(context: Mapping) -> str:
    """Report writer used by ``tametools sync`` for WESTGARD_QC results."""
    report, result = context["report"], context["result"]
    settings = result.meta.get("PLUGIN", {}) if isinstance(result.meta.get("PLUGIN", {}), Mapping) else {}
    options = report_options(settings.get("REPORT"))
    project = str(report.project).replace("\\", "/").rsplit("/", 1)[-1]
    sync = {"project": project, "source": report.source.get("path", ""), "sequence": report.sequence, "data": report.data,
            "result": report.result, "warnings": report.warnings, "reasons": report.reasons, "history": context.get("history", []),
            "synced_at": report.timestamp}
    return render_qc_report(result.df, settings=settings, title=options.get("TITLE", f"정도관리(QC) 보고서 · {project.rsplit('.', 1)[0]}"),
                            sync=sync, generated_at=str(context.get("generated_at") or report.timestamp),
                            last_runs=options.get("LAST_RUNS", DEFAULT_LAST_RUNS), tool_version=str(context.get("tool_version", "")),
                            records=list(context.get("records") or []))


def result_view(result) -> str:
    """Screen for a WESTGARD_QC result shown on its own (web workbench, ``run-plugin --view``).

    The same status table, Levey-Jennings charts and flag list as the sync report. What belongs to a sync
    project (what the last sync changed, action and review records) is left out, because there is none here.
    """
    settings = result.meta.get("PLUGIN", {}) if isinstance(result.meta.get("PLUGIN", {}), Mapping) else {}
    options = report_options(settings.get("REPORT"))
    return render_qc_report(result.df, settings=settings, title=options.get("TITLE", "정도관리(QC) 보고서"),
                            last_runs=options.get("LAST_RUNS", DEFAULT_LAST_RUNS), standalone=True)


def render_qc_report(result: pd.DataFrame, *, settings: Mapping | None = None, title: str = "QC 보고서",
                     sync: Mapping | None = None, generated_at: str = "", last_runs: int = DEFAULT_LAST_RUNS,
                     tool_version: str = "", records: list[Mapping] | None = None, standalone: bool = False) -> str:
    """Build the report from a WESTGARD_QC result table.

    ``sync`` carries what the last ``tametools sync`` changed (optional).
    ``settings`` is the result's ``META[PLUGIN]`` (rule sets actually applied).
    ``records`` are the action and review records kept in the project (optional).
    ``standalone`` leaves out everything about records: the result is shown outside a sync project.
    """
    from tametools.plugin_api import verify_records

    settings = settings or {}
    records = [record for record in (records or []) if isinstance(record, Mapping)]
    if isinstance(last_runs, bool) or not isinstance(last_runs, int) or last_runs < 2:
        raise ValueError("The QC report needs LAST_RUNS >= 2")
    frame = _prepare(result)
    window = _window(frame, last_runs)
    html = ["<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
            f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>", f"<h1>{escape(title)}</h1>"]
    if frame.empty:
        html.append("<p class='sub'>아직 관리물질 측정이 없습니다.</p></main></body></html>")
        return "".join(html)
    first, last = window["_moment"].min(), window["_moment"].max()
    period = (_text(window.loc[window["_moment"].eq(first), "time"].iloc[0])[:10] + " ~ "
              + _text(window.loc[window["_moment"].eq(last), "time"].iloc[0])[:10])
    meta = [("기간", period), ("장비", ", ".join(dict.fromkeys(window["instrument"]))),
            ("검사", ", ".join(dict.fromkeys(window["_name"])))]
    if sync:
        meta += [("프로젝트", sync.get("project", "")), ("원본", sync.get("source", "")),
                 ("동기화", f"#{sync.get('sequence', '')}" + (f" ({_when(sync['synced_at'])})" if sync.get("synced_at") else ""))]
    if generated_at:
        meta.append(("작성", _when(generated_at)))
    if tool_version:
        meta.append(("tametools", tool_version))
    html.append("<div class='meta'>" + "".join(f"<span>{escape(label)} <b>{escape(str(value))}</b></span>" for label, value in meta if value) + "</div>")
    html.append("<p class='notice'>이 보고서는 선언한 기준값과 Westgard 규칙으로 관리 run 을 다시 계산해 보여 주는 <b>수동 검토용</b> 자료입니다. "
                "환자 결과의 보고 허용·보류를 자동으로 결정하지 않으며, 거부된 run 의 원인 조사와 조치는 검사실 절차에 따릅니다.</p>")
    if not standalone:
        html.append(_open_notice(frame, records))
    broken = verify_records(list(records))
    if broken:
        html.append("<p class='notice alert'><b>기록 확인 필요</b>: 프로젝트에 남긴 기록 #" + escape(", #".join(str(item) for item in broken))
                    + " 이(가) 도구 밖에서 수정되었거나 빠졌습니다.</p>")
    if sync:
        html.append(_sync_section(sync, frame))
    statistics = _material_statistics(window)
    html.append(_status_section(statistics, last_runs))
    html.append(_chart_section(window))
    html.append(_flag_section(window, records, standalone))
    html.append(_settings_section(settings, window))
    if sync:
        html.append(_history_section(sync.get("history", [])))
    if standalone:
        html.append("<p class='small'>조치 기록과 검토 기록은 이 결과를 동기화 프로젝트(<b>tametools sync</b>)로 관리할 때 남기고 볼 수 있습니다.</p>")
    else:
        html.append(_review_section(records, period[:10], period[-10:], numbered=bool(sync)))
    html.append(f"<div id='tip' role='status'></div><script>{_SCRIPT}</script></main></body></html>")
    return "".join(html)

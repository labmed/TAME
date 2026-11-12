"""내보내기 형식을 더하는 플러그인 예제 — Markdown 표.

분석이 아니라 "어떤 형식으로 내보낼 수 있는가"를 더하는 플러그인이다. `register_exporter` 로 쓰는 함수 하나를 등록하면
`tametools export` 가 그 형식을 알게 된다. tametools 의 명령줄 코드는 고치지 않는다.

실행 방법 (저장소 루트에서)
---------------------------
    set TAMETOOLS_PLUGIN_PATH=tutorial\\plugin_examples
    tametools export tutorial/05_plugins/sample_dialog_plugin.tame table.md --allow-plugins
    tametools export tutorial/05_plugins/sample_dialog_plugin.tame table.txt --format markdown --allow-plugins
    tametools plugins --allow-plugins          # 끝의 "plugin export formats: markdown (.md)"

지켜야 할 것
------------
- 쓰는 함수는 `write(dataset, path)` 다. `path` 에 파일을 쓰고, 그 파일 하나만 썼으면 아무것도 돌려주지 않는다.
  파일을 여러 개 쓰면 쓴 경로들을 리스트로 돌려준다.
- 값은 `export_frame(dataset)` 으로 얻는다. tametools 의 csv/tsv 내보내기와 칸 하나하나가 같은 글자가 된다
  (결측·공백 상태 표기, 성별 표준화 포함).
- tametools 가 원래 가진 형식 이름(csv, tsv, jsonl, sql, parquet, feather, r_bundle)과 그 확장자는 가져갈 수 없다.
- 작성 계약 전체: ``docs/PLUGIN_AUTHOR_GUIDE.md`` 12절.
"""
from __future__ import annotations

from tametools.plugin_api import export_frame, register_exporter


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


@register_exporter("markdown", suffixes=(".md",), description="Markdown table, for pasting a small table into a document or an issue")
def write_markdown(dataset, path) -> None:
    frame = export_frame(dataset)
    lines = ["| " + " | ".join(_cell(name) for name in frame.columns) + " |",
             "| " + " | ".join("---" for _ in frame.columns) + " |"]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in frame.itertuples(index=False)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

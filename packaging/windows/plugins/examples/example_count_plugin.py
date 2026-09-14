"""tametools 외부 플러그인 최소 예제 — 플러그인 작성법 튜토리얼.

이 파일은 "새 분석 기능을 플러그인으로 추가하는 가장 작은 형태"를 보여준다. 검사항목별 건수를
세는 단순 기능이지만, 모든 tametools 플러그인이 공유하는 5가지 핵심 계약을 그대로 담고 있다.

1. 등록(@register_plugin)
   - `@register_plugin("COUNT_BY_TEST", description=...)` 로 이름을 붙여 등록한다.
   - 이름은 CLI `run-plugin <파일> COUNT_BY_TEST` 와 META `[ANALYSES]` 에서 그대로 쓰인다.

2. 핸들러 시그니처
   - `handler(dataset, meta, step_name, options) -> OperationOutput`
   - dataset: DATA+META를 가진 TameDataset / meta: META 전체 / step_name: 실행 단계명 /
     options: CLI `--option KEY=VALUE` 와 META 옵션을 합친 dict.

3. options 로 동작을 바꾼다
   - `ci_get(options, "MIN_COUNT", 0)` 처럼 옵션을 읽는다(대소문자 무시는 ci_get 이 처리).
   - 예: `--option MIN_COUNT=5` 면 건수 5 미만 항목을 제외한다.

4. 컬럼명이 아니라 "태그"로 컬럼을 찾는다(핵심)
   - `dataset.first_column_with_tag("TESTNAME")` 처럼 역할 태그로 대상 컬럼을 고른다.
   - 그래서 컬럼명이 검사항목명/test_name/analyte 무엇이든 같은 플러그인이 동작한다.
   - 별칭 처리: TESTNAME 이 없으면 ITEM 으로 fallback (`... or first_column_with_tag("ITEM")`).
   - 필요한 태그가 없으면 크래시하지 말고 graceful 하게(여기서는 전체 1행 집계) 처리하고
     `warnings` 로 사용자에게 알린다.

5. 결과도 태그가 붙은 TAME 데이터셋으로 돌려준다
   - 출력 헤더를 `[[TAG::TAG]]표시명` 으로 적고 `merged_column_specs(headers)` 로 ColumnSpec을 만든다.
   - 결과가 다시 .tame 으로 저장되어 describe/merge/다른 플러그인의 입력으로 연쇄 사용된다.
   - `OperationOutput` 은 table 외에 dataset, tables(다중 표), charts, files, warnings, message 도
     실을 수 있다(이 최소 예제는 dataset/table/warnings/message 를 사용; charts/files/tables 는 미사용).

실행 방법
---------
이 모듈은 META 의 `[PLUGINS].MODULES` 에 등록해 로드한다.

    [PLUGINS]
    MODULES = ["tutorial.plugin_examples.example_count_plugin"]

tametools 를 설치하면(`pip install ./tametools`) `tametools` 명령을 그대로 쓴다.
외부 Python 플러그인은 보안상 기본 비활성이라 신뢰하는 로컬 파일에 한해 ``--allow-plugins`` 로 허용한다.

    tametools run-plugin DATA.tame COUNT_BY_TEST --allow-plugins --option MIN_COUNT=2 --output out.tame

단, 등록한 플러그인 "모듈"은 파이썬 import 경로 위에 있어야 한다. tametools 자체는 설치돼 있어도
이 예제 모듈(``tutorial.plugin_examples.example_count_plugin``)은 저장소 안에 있으므로, 저장소
루트에서 실행하며 ``PYTHONPATH=.`` 로 루트를 import 경로에 포함시킨다(없으면 "No module named
'tutorial'" 로 깔끔히 실패한다).

    PYTHONPATH=. tametools run-plugin \\
      tutorial/05_plugins/sample_reference_interval.tame COUNT_BY_TEST \\
      --allow-plugins --option MIN_COUNT=2 --output out.tame

(직접 만든 플러그인을 site-packages 등 import 경로에 두면 ``PYTHONPATH`` 없이 ``tametools`` 만으로 실행된다.)

더 알아보기
-----------
- 작성 계약 전체: ``docs/PLUGIN_AUTHOR_GUIDE.md``
- 차트+표+docx 보고서까지 만드는 완성형 예시: ``tutorial/plugin_examples/combined_report_plugin.py``
  (``COMBINED_CHEMISTRY_REPORT``), 동봉 플러그인 수준 예시: ``tametools/src/tametools/plugins/lab_tat.py``.
"""
from __future__ import annotations

import pandas as pd

from tametools.config import ci_get
from tametools.models import OperationOutput, TameDataset, merged_column_specs
from tametools.plugin_base.base import register_plugin


@register_plugin(
    "COUNT_BY_TEST",
    description="Example external plugin: count rows per test (TESTNAME/ITEM), with an optional MIN_COUNT filter.",
)
def count_by_test_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    warnings: list[str] = []

    # (4) 컬럼명이 아니라 역할 태그로 대상 컬럼을 찾는다. 없으면 graceful 처리 + 경고.
    test_column = dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")
    if test_column is None:
        warnings.append("No TESTNAME/ITEM tagged column found; counted all rows as a single 'ALL' group.")
        frame = pd.DataFrame([{"검사항목명": "ALL", "대상수": int(len(dataset.df))}], columns=["검사항목명", "대상수"])
    else:
        frame = (
            dataset.df.groupby(test_column.name, dropna=False)
            .size()
            .reset_index(name="대상수")
            .rename(columns={test_column.name: "검사항목명"})
        )

    # (3) options 사용: --option MIN_COUNT=5 처럼 옵션을 읽어 동작을 바꾼다.
    min_count = int(ci_get(options, "MIN_COUNT", 0))
    if min_count > 0:
        before = len(frame)
        frame = frame.loc[frame["대상수"] >= min_count].reset_index(drop=True)
        dropped = before - len(frame)
        if dropped:
            warnings.append(f"MIN_COUNT={min_count}: dropped {dropped} test(s) below the threshold.")

    # (5) 결과도 태그가 붙은 TAME 데이터셋으로 만든다(헤더에 [[TAG::TAG]]표시명).
    columns = merged_column_specs(["[[TESTNAME::ITEM]]검사항목명", "[[N::NUM]]대상수"])
    frame = frame[["검사항목명", "대상수"]]
    frame.columns = [column.name for column in columns]
    result = TameDataset(
        df=frame,
        columns=columns,
        # schema/job/raw_sections 는 TameDataset 기본값(빈 dict)을 그대로 쓴다.
        meta={"PLUGIN": {"NAME": "COUNT_BY_TEST", "MIN_COUNT": min_count}},
        source_path=dataset.source_path,
    )
    return OperationOutput(
        name=step_name,
        dataset=result,
        table=result.df,
        warnings=warnings,
        message=f"count_by_test rows={len(result.df)} min_count={min_count}",
    )

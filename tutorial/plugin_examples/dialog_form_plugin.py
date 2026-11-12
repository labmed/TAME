"""질문을 선언하는 플러그인 예제 — 빠진 설정을 창(대화상자)으로 묻는다.

플러그인은 창을 직접 만들지 않는다. 물어볼 것을 ``form`` 으로 **선언**하면 실행하는 쪽이 상황에 맞게 보여 준다.

- 데스크톱: 대화상자 창
- 창을 띄울 수 없는 터미널: 같은 질문을 글로
- 사람이 없는 실행(스케줄러, 파이프, 테스트): 묻지 않고, 지정해야 할 옵션 이름을 알려 주며 멈춤

답은 플러그인 옵션에 합쳐져 ``options`` 로 들어오므로 핸들러는 창이 있었는지 알 필요가 없고,
실행 이력(LOG)에도 다른 옵션과 똑같이 남는다. 그래서 같은 실행을 ``--option`` 만으로 다시 할 수 있다.

실행 방법 (저장소 루트에서)
---------------------------
    # 필수 설정(GROUP)이 빠졌으므로 터미널에서 실행하면 창이 뜬다
    PYTHONPATH=. tametools run-plugin tutorial/05_plugins/sample_dialog_plugin.tame COUNT_BY_COLUMN --allow-plugins

    # 다 지정하면 묻지 않는다. --dialog 를 붙이면 지금 값을 채운 창이 뜬다
    PYTHONPATH=. tametools run-plugin tutorial/05_plugins/sample_dialog_plugin.tame COUNT_BY_COLUMN --allow-plugins \\
      --option GROUP=성별 --option MIN_COUNT=2

지켜야 할 것
------------
- 핸들러 안에서 창을 띄우거나 ``tametools.dialogs.ask`` 를 부르지 않는다(실행 중에는 막혀 있다).
  결과는 자료와 옵션만으로 정해져야 다시 계산해도 같은 값이 나온다.
- ``required=True`` 는 "이 값 없이는 실행할 수 없다"는 뜻이다. 기본값이나 태그로 대신할 수 있으면 붙이지 않는다.
- 작성 계약 전체: ``docs/PLUGIN_AUTHOR_GUIDE.md`` 11절.
"""
from __future__ import annotations

from tametools.plugin_api import OperationOutput, TameDataset, ci_get, field, form, merged_column_specs, register_plugin

# 물어볼 것. key 가 곧 옵션 이름이다(--option GROUP=성별 과 같은 자리).
QUESTIONS = form("열별 건수", [
    field("GROUP", "묶을 열", kind="column", required=True, hint="이 열의 값마다 행 수를 셉니다"),
    field("MIN_COUNT", "최소 건수", kind="integer", default=0, minimum=0, hint="이보다 적은 값은 표에서 뺍니다"),
    field("ORDER", "정렬", kind="choice", choices=[("COUNT", "건수 많은 순"), ("NAME", "이름 순")], default="COUNT"),
], intro="어느 열을 기준으로 셀지 정합니다.", submit="실행")


@register_plugin(
    "COUNT_BY_COLUMN",
    description="Example external plugin that declares its questions: count rows per value of a chosen column.",
    form=QUESTIONS,
)
def count_by_column_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    # 창에서 온 값(정수 3)과 --option 으로 온 값(글자 "3")을 모두 받는다.
    group = str(ci_get(options, "GROUP", "")).strip()
    if group not in [column.name for column in dataset.columns]:
        raise ValueError(f"COUNT_BY_COLUMN: GROUP 은 자료의 열 이름이어야 합니다: {group!r}")
    min_count = int(ci_get(options, "MIN_COUNT", 0))
    order = str(ci_get(options, "ORDER", "COUNT")).strip().upper()

    frame = dataset.df.groupby(group, dropna=False, observed=False).size().reset_index(name="count")
    frame = frame.loc[frame["count"] >= min_count]
    frame = frame.sort_values(group) if order == "NAME" else frame.sort_values(["count", group], ascending=[False, True])

    columns = merged_column_specs(["[[STR]]값", "[[N::NUM]]건수"])
    frame = frame[[group, "count"]].reset_index(drop=True)
    frame.columns = [column.name for column in columns]
    result = TameDataset(df=frame, columns=columns, source_path=dataset.source_path,
                         meta={"PLUGIN": {"NAME": "COUNT_BY_COLUMN", "GROUP": group, "MIN_COUNT": min_count, "ORDER": order}})
    return OperationOutput(name=step_name, dataset=result, table=result.df,
                           message=f"count_by_column group={group} rows={len(result.df)} min_count={min_count} order={order}")

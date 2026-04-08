from __future__ import annotations

import pandas as pd

from tametools.models import OperationOutput, TameDataset, merged_column_specs
from tametools.plugins.base import register_plugin


@register_plugin(
    "COUNT_BY_TEST",
    description="Example external plugin that counts rows by TESTNAME or ITEM.",
)
def count_by_test_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    test_column = dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")
    if test_column is None:
        frame = pd.DataFrame(
            [{"검사항목명": "ALL", "대상수": int(len(dataset.df))}],
            columns=["검사항목명", "대상수"],
        )
    else:
        frame = (
            dataset.df.groupby(test_column.name, dropna=False)
            .size()
            .reset_index(name="대상수")
            .rename(columns={test_column.name: "검사항목명"})
        )

    headers = [
        "[[TESTNAME::ITEM]]검사항목명",
        "[[N::NUM]]대상수",
    ]
    columns = merged_column_specs(headers)
    frame = frame[["검사항목명", "대상수"]]
    frame.columns = [column.name for column in columns]
    result = TameDataset(
        df=frame,
        columns=columns,
        meta={"PLUGIN": {"NAME": "COUNT_BY_TEST"}},
        schema={},
        job={},
        raw_sections={},
        source_path=dataset.source_path,
    )
    return OperationOutput(
        name=step_name,
        dataset=result,
        table=result.df,
        message=f"count_by_test rows={len(result.df)}",
    )

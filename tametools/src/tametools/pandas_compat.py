from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd


def concat_dataframes(frames: Iterable[pd.DataFrame | pd.Series], **kwargs: Any) -> pd.DataFrame:
    frame_list = list(frames)
    if not frame_list:
        return pd.DataFrame()

    axis = kwargs.get("axis", 0)
    if axis not in (0, "index"):
        return pd.concat(frame_list, **kwargs)

    columns: list[Any] = []
    seen: set[Any] = set()
    normalized: list[pd.DataFrame] = []
    for frame in frame_list:
        for column in frame.columns:
            if column not in seen:
                columns.append(column)
                seen.add(column)
        if frame.empty:
            normalized.append(frame.iloc[:, 0:0])
        else:
            normalized.append(frame.dropna(axis=1, how="all"))

    result = pd.concat(normalized, **kwargs)
    return result.reindex(columns=columns) if columns else result

"""Hash effective controls, not only the original source file and cell matrix."""
from copy import deepcopy
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path

import numpy as np

from .observation_contract import CAPABILITIES


def _encode(value):
    if isinstance(value, (datetime, date, time)):
        return {"__tame_type__": type(value).__name__, "iso": value.isoformat()}
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError("Unsupported effective metadata value: " + type(value).__name__)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False, default=_encode)


def effective_input_snapshot(dataset):
    return dict(version=1, meta=deepcopy(dataset.meta), schema=deepcopy(dataset.schema), job=deepcopy(dataset.job),
        columns=[dict(name=c.name, original_header=c.original_header, tags=list(c.tags),
                      effective_tags=list(dataset.effective_column_tags(c))) for c in dataset.columns],
        supported_capabilities=CAPABILITIES)


def source_hashes():
    root = Path(__file__).parent
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*.py"))}

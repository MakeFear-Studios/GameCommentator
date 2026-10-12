"""Turn arbitrary log data into JSON without ever crashing the logger."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """``json.dumps`` fallback for values it can't encode natively."""
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, PurePath):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    if isinstance(obj, (bytes, bytearray)):
        return {"bytes": len(obj)}
    shape = getattr(obj, "shape", None)
    if shape is not None and hasattr(obj, "dtype"):
        # numpy-like: scalars become plain numbers, arrays become a summary (never the pixels).
        if shape == () and hasattr(obj, "item"):
            return obj.item()
        return {"array": {"shape": list(shape), "dtype": str(obj.dtype)}}
    return str(obj)


def dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, default=to_jsonable, ensure_ascii=False)

"""引擎公共类型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunSummary:
    """一次引擎运行的统计结果。"""

    source: str
    new: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

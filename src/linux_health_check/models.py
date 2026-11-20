from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .status import Status


@dataclass
class CheckResult:
    name: str
    status: Status
    summary: str
    details: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.name,
            "summary": self.summary,
            "details": self.details,
            "metrics": self.metrics,
            "notes": self.notes,
        }

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .status import Status


@dataclass
class CheckResult:
    name: str
    status: Status
    summary: str
    details: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.name,
            "summary": self.summary,
            "details": self.details,
            "metrics": self.metrics,
            "notes": self.notes,
        }

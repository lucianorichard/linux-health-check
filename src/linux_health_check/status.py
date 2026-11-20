"""Check statuses and their exit codes (Nagios/Icinga plugin convention)."""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable


class Status(IntEnum):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    UNKNOWN = 3

    @property
    def tag(self) -> str:
        return {"OK": " OK ", "WARNING": "WARN", "CRITICAL": "CRIT", "UNKNOWN": "UNKN"}[self.name]


# Severity order used to combine results. An actionable problem (WARNING,
# CRITICAL) outranks missing data (UNKNOWN), so a critical disk is never hidden
# behind a check that simply could not run.
_SEVERITY = {Status.OK: 0, Status.UNKNOWN: 1, Status.WARNING: 2, Status.CRITICAL: 3}


def worst(statuses: Iterable[Status]) -> Status:
    """Return the most severe status, or OK for an empty input."""
    return max(statuses, key=_SEVERITY.__getitem__, default=Status.OK)

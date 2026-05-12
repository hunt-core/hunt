from __future__ import annotations

from typing import Callable


class TrendMetric:
    """A time-series trend metric for charts."""

    metric_type: str = "trend"

    def __init__(self, name: str, resolver: Callable[[str], dict]) -> None:
        """
        Args:
            name: Display name.
            resolver: Called with a period string ("day", "week", "month", "year")
                      and must return {"labels": [...], "values": [...]}.
        """
        self.name = name
        self._resolver = resolver

    def calculate(self, period: str = "week") -> dict:
        try:
            data = self._resolver(period)
        except Exception:
            data = {"labels": [], "values": []}

        return {
            "name": self.name,
            "period": period,
            "labels": data.get("labels", []),
            "values": data.get("values", []),
            "metric_type": self.metric_type,
        }

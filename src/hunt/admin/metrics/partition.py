from __future__ import annotations

from typing import Callable


class PartitionMetric:
    """A partition/donut metric that breaks data into labelled segments."""

    metric_type: str = "partition"

    def __init__(self, name: str, resolver: Callable[[], list[dict]]) -> None:
        """
        Args:
            name: Display name.
            resolver: Called with no arguments, must return a list of
                      {"label": str, "value": int|float} dicts.
        """
        self.name = name
        self._resolver = resolver

    def calculate(self) -> dict:
        try:
            segments = self._resolver()
        except Exception:
            segments = []

        total = sum(s.get("value", 0) for s in segments) or 1
        enriched = []
        for s in segments:
            val = s.get("value", 0)
            enriched.append({
                "label": s.get("label", ""),
                "value": val,
                "percentage": round((val / total) * 100, 1),
            })

        return {
            "name": self.name,
            "segments": enriched,
            "total": total,
            "metric_type": self.metric_type,
        }

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ValueMetric:
    """A single numeric metric with optional trend comparison."""

    metric_type: str = "value"

    def __init__(
        self,
        name: str,
        resolver: Callable[[], Any],
        *,
        prefix: str = "",
        suffix: str = "",
        trend_resolver: Callable[[], Any] | None = None,
        trend_label: str = "",
    ) -> None:
        self.name = name
        self._resolver = resolver
        self.prefix = prefix
        self.suffix = suffix
        self._trend_resolver = trend_resolver
        self.trend_label = trend_label

    def calculate(self) -> dict:
        try:
            value = self._resolver()
        except Exception:
            value = 0

        formatted = f"{self.prefix}{value}{self.suffix}"

        previous = None
        change = None
        change_type = None

        if self._trend_resolver is not None:
            try:
                previous = self._trend_resolver()
                if previous is not None and previous != 0:
                    change = round(((value - previous) / previous) * 100, 1)
                    change_type = "increase" if change >= 0 else "decrease"
                elif previous == 0:
                    change = None
                    change_type = "neutral"
            except Exception:
                previous = None

        return {
            "name": self.name,
            "value": value,
            "formatted": formatted,
            "previous": previous,
            "change": change,
            "change_type": change_type,
            "prefix": self.prefix,
            "suffix": self.suffix,
            "trend_label": self.trend_label,
            "metric_type": self.metric_type,
        }

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")


class Collection:
    def __init__(self, items: Iterable[Any] = ()) -> None:
        self._items: list[Any] = list(items)

    # ------------------------------------------------------------------
    # Transformation
    # ------------------------------------------------------------------

    def map(self, callback: Callable) -> Collection:
        return Collection(callback(item) for item in self._items)

    def filter(self, callback: Callable | None = None) -> Collection:
        if callback is None:
            return Collection(item for item in self._items if item)
        return Collection(item for item in self._items if callback(item))

    def reject(self, callback: Callable) -> Collection:
        return Collection(item for item in self._items if not callback(item))

    def each(self, callback: Callable) -> Collection:
        for item in self._items:
            callback(item)
        return self

    def flat_map(self, callback: Callable) -> Collection:
        result = []
        for item in self._items:
            result.extend(callback(item))
        return Collection(result)

    def pluck(self, key: str) -> Collection:
        def _get(item: Any) -> Any:
            if isinstance(item, dict):
                return item.get(key)
            return getattr(item, key, None)

        return Collection(_get(item) for item in self._items)

    def unique(self, key: str | None = None) -> Collection:
        seen: set = set()
        result = []
        for item in self._items:
            val = item if key is None else (item.get(key) if isinstance(item, dict) else getattr(item, key))
            if val not in seen:
                seen.add(val)
                result.append(item)
        return Collection(result)

    def sort_by(self, key: str | Callable, descending: bool = False) -> Collection:
        if callable(key):
            fn = key
        else:

            def fn(item: Any) -> Any:
                return item.get(key) if isinstance(item, dict) else getattr(item, key)

        return Collection(sorted(self._items, key=fn, reverse=descending))

    def group_by(self, key: str | Callable) -> dict[Any, Collection]:
        groups: dict[Any, list] = {}
        fn = key if callable(key) else (lambda item: item.get(key) if isinstance(item, dict) else getattr(item, key))
        for item in self._items:
            k = fn(item)
            groups.setdefault(k, []).append(item)
        return {k: Collection(v) for k, v in groups.items()}

    def chunk(self, size: int) -> Collection:
        chunks = [self._items[i : i + size] for i in range(0, len(self._items), size)]
        return Collection(Collection(c) for c in chunks)

    def take(self, n: int) -> Collection:
        return Collection(self._items[:n])

    def skip(self, n: int) -> Collection:
        return Collection(self._items[n:])

    def reverse(self) -> Collection:
        return Collection(reversed(self._items))

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def count(self) -> int:
        return len(self._items)

    def first(self, callback: Callable | None = None) -> Any:
        if callback:
            for item in self._items:
                if callback(item):
                    return item
            return None
        return self._items[0] if self._items else None

    def last(self, callback: Callable | None = None) -> Any:
        if callback:
            for item in reversed(self._items):
                if callback(item):
                    return item
            return None
        return self._items[-1] if self._items else None

    def contains(self, key_or_callback: Any, value: Any = None) -> bool:
        if callable(key_or_callback):
            return any(key_or_callback(item) for item in self._items)
        if value is not None:
            return any(
                (item.get(key_or_callback) if isinstance(item, dict) else getattr(item, key_or_callback)) == value
                for item in self._items
            )
        return key_or_callback in self._items

    def sum(self, key: str | Callable | None = None) -> Any:
        if key is None:
            return sum(self._items)
        fn = key if callable(key) else (lambda item: item.get(key) if isinstance(item, dict) else getattr(item, key))
        return sum(fn(item) for item in self._items)

    def max(self, key: str | Callable | None = None) -> Any:
        if key is None:
            return max(self._items)
        fn = key if callable(key) else (lambda item: item.get(key) if isinstance(item, dict) else getattr(item, key))
        return max(fn(item) for item in self._items)

    def min(self, key: str | Callable | None = None) -> Any:
        if key is None:
            return min(self._items)
        fn = key if callable(key) else (lambda item: item.get(key) if isinstance(item, dict) else getattr(item, key))
        return min(fn(item) for item in self._items)

    def avg(self, key: str | Callable | None = None) -> float:
        total = self.sum(key)
        return total / len(self._items) if self._items else 0.0

    def is_empty(self) -> bool:
        return len(self._items) == 0

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def to_list(self) -> list:
        return list(self._items)

    def to_dict(self, key: str) -> dict:
        result = {}
        for item in self._items:
            k = item.get(key) if isinstance(item, dict) else getattr(item, key)
            result[k] = item
        return result

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"Collection({self._items!r})"

    @classmethod
    def make(cls, items: Iterable[Any] = ()) -> Collection:
        return cls(items)

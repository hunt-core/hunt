from __future__ import annotations

import time
from typing import Any, ClassVar

from hunt.database.query_builder import QueryBuilder
from hunt.support.str import Str


class ModelMeta(type):
    """Metaclass that injects table name and sets up the class."""

    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> type:
        cls = super().__new__(mcs, name, bases, namespace)
        if name != "Model" and "table" not in namespace:
            cls.table = Str.plural(Str.snake(name)).lower()
        return cls


class Model(metaclass=ModelMeta):
    # ------------------------------------------------------------------
    # Class-level configuration
    # ------------------------------------------------------------------
    table: ClassVar[str] = ""
    primary_key: ClassVar[str] = "id"
    fillable: ClassVar[list[str]] = []
    guarded: ClassVar[list[str]] = ["id"]
    hidden: ClassVar[list[str]] = []
    casts: ClassVar[dict[str, str]] = {}
    timestamps: ClassVar[bool] = True
    _soft_deletes: ClassVar[bool] = False
    _connection: ClassVar[str | None] = None

    _event_listeners: ClassVar[dict[str, list]] = {}

    def __init__(self, attributes: dict | None = None) -> None:
        self._attributes: dict[str, Any] = {}
        self._original: dict[str, Any] = {}
        self._exists: bool = False
        self._relations: dict[str, Any] = {}
        if attributes:
            self.fill(attributes)

    # ------------------------------------------------------------------
    # Attribute access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Accessor pattern: get_<name>_attribute
        accessor = f"get_{name}_attribute"
        if accessor in type(self).__dict__:
            return type(self).__dict__[accessor](self)
        if name in self.__dict__.get("_attributes", {}):
            return self._cast(name, self._attributes[name])
        if name in self.__dict__.get("_relations", {}):
            return self._relations[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name in type(self).__dict__:
            super().__setattr__(name, value)
            return
        # Mutator pattern: set_<name>_attribute
        mutator = f"set_{name}_attribute"
        if mutator in type(self).__dict__:
            type(self).__dict__[mutator](self, value)
            return
        self._attributes[name] = value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._attributes!r})"

    # ------------------------------------------------------------------
    # Fill
    # ------------------------------------------------------------------

    def fill(self, attributes: dict) -> "Model":
        for key, value in attributes.items():
            if self._is_fillable(key):
                self._attributes[key] = value
        return self

    def force_fill(self, attributes: dict) -> "Model":
        self._attributes.update(attributes)
        return self

    def _is_fillable(self, key: str) -> bool:
        if self.fillable:
            return key in self.fillable
        return key not in self.guarded

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> bool:
        if self.timestamps:
            now = int(time.time())
            if not self._exists:
                self._attributes.setdefault("created_at", now)
            self._attributes["updated_at"] = now

        if self._exists:
            self._fire_event("updating")
            dirty = self._dirty()
            if dirty:
                self.query().where(self.primary_key, self._attributes[self.primary_key]).update(dirty)
            self._fire_event("updated")
        else:
            self._fire_event("creating")
            pk = self.query().insert(self._attributes)
            self._attributes[self.primary_key] = pk
            self._exists = True
            self._fire_event("created")

        self._original = dict(self._attributes)
        return True

    def delete(self) -> bool:
        if not self._exists:
            return False
        self._fire_event("deleting")
        if self._soft_deletes:
            now = int(time.time())
            self.query().where(self.primary_key, self._attributes[self.primary_key]).update({"deleted_at": now})
            self._attributes["deleted_at"] = now
        else:
            self.query().where(self.primary_key, self._attributes[self.primary_key]).delete()
            self._exists = False
        self._fire_event("deleted")
        return True

    def restore(self) -> bool:
        if not self._soft_deletes:
            return False
        self.query().with_trashed().where(self.primary_key, self._attributes[self.primary_key]).update({"deleted_at": None})
        self._attributes["deleted_at"] = None
        return True

    def force_delete(self) -> bool:
        self.query().where(self.primary_key, self._attributes[self.primary_key]).delete()
        self._exists = False
        return True

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _dirty(self) -> dict:
        dirty = {}
        for k, v in self._attributes.items():
            if k not in self._original or self._original[k] != v:
                dirty[k] = v
        return dirty

    def is_dirty(self, *keys: str) -> bool:
        dirty = self._dirty()
        if not keys:
            return bool(dirty)
        return any(k in dirty for k in keys)

    # ------------------------------------------------------------------
    # Class-level query methods
    # ------------------------------------------------------------------

    @classmethod
    def query(cls) -> QueryBuilder:
        return QueryBuilder(cls.table, cls, cls._connection)

    @classmethod
    def all(cls) -> list["Model"]:
        return cls.query().get()

    @classmethod
    def find(cls, id: Any) -> "Model | None":
        return cls.query().find(id)

    @classmethod
    def find_or_fail(cls, id: Any) -> "Model":
        instance = cls.find(id)
        if instance is None:
            raise ValueError(f"{cls.__name__} not found with id={id}")
        return instance

    @classmethod
    def where(cls, column: str, operator_or_value: Any, value: Any = None) -> QueryBuilder:
        return cls.query().where(column, operator_or_value, value)

    @classmethod
    def create(cls, attributes: dict) -> "Model":
        instance = cls(attributes)
        instance.save()
        return instance

    @classmethod
    def first_or_create(cls, search: dict, attributes: dict | None = None) -> tuple["Model", bool]:
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            return existing, False
        merged = {**search, **(attributes or {})}
        return cls.create(merged), True

    @classmethod
    def update_or_create(cls, search: dict, values: dict) -> "Model":
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            existing.fill(values)
            existing.save()
            return existing
        return cls.create({**search, **values})

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def has_one(self, related: type, foreign_key: str | None = None, local_key: str | None = None) -> Any:
        from hunt.database.relations.has_one import HasOne
        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        lk = local_key or self.primary_key
        return HasOne(related, self, fk, lk).get_result()

    def has_many(self, related: type, foreign_key: str | None = None, local_key: str | None = None) -> list:
        from hunt.database.relations.has_many import HasMany
        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        lk = local_key or self.primary_key
        return HasMany(related, self, fk, lk).get_results()

    def belongs_to(self, related: type, foreign_key: str | None = None, owner_key: str | None = None) -> Any:
        from hunt.database.relations.belongs_to import BelongsTo
        fk = foreign_key or f"{Str.snake(related.__name__)}_id"
        ok = owner_key or "id"
        return BelongsTo(related, self, fk, ok).get_result()

    def belongs_to_many(
        self,
        related: type,
        pivot_table: str | None = None,
        foreign_key: str | None = None,
        related_key: str | None = None,
    ) -> list:
        from hunt.database.relations.belongs_to_many import BelongsToMany
        pivot = pivot_table or "_".join(sorted([
            Str.snake(type(self).__name__),
            Str.snake(related.__name__),
        ]))
        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        rk = related_key or f"{Str.snake(related.__name__)}_id"
        return BelongsToMany(related, self, pivot, fk, rk).get_results()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        data = {k: v for k, v in self._attributes.items() if k not in self.hidden}
        for rel_name, rel_value in self._relations.items():
            if isinstance(rel_value, Model):
                data[rel_name] = rel_value.to_dict()
            elif isinstance(rel_value, list):
                data[rel_name] = [r.to_dict() if isinstance(r, Model) else r for r in rel_value]
            else:
                data[rel_name] = rel_value
        return data

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), default=str)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    @classmethod
    def observe(cls, observer: Any) -> None:
        for event in ("creating", "created", "updating", "updated", "deleting", "deleted"):
            if hasattr(observer, event):
                cls._on(event, getattr(observer, event))

    @classmethod
    def _on(cls, event: str, callback: Any) -> None:
        key = f"{cls.__name__}.{event}"
        cls._event_listeners.setdefault(key, []).append(callback)

    def _fire_event(self, event: str) -> None:
        key = f"{type(self).__name__}.{event}"
        for cb in type(self)._event_listeners.get(key, []):
            cb(self)

    # ------------------------------------------------------------------
    # Casting
    # ------------------------------------------------------------------

    def _cast(self, key: str, value: Any) -> Any:
        cast_type = self.casts.get(key)
        if cast_type is None or value is None:
            return value
        if cast_type == "int" or cast_type == "integer":
            return int(value)
        if cast_type == "float":
            return float(value)
        if cast_type == "bool" or cast_type == "boolean":
            return bool(value)
        if cast_type == "str" or cast_type == "string":
            return str(value)
        if cast_type == "json" or cast_type == "array":
            import json
            return json.loads(value) if isinstance(value, str) else value
        return value

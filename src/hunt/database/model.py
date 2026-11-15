from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, ClassVar

# _EAGER_MODE: when True, relationship helper methods return the Relation object
# instead of executing the query. Used internally by the eager-loading
# infrastructure. ``relation`` optionally marks a method as a relationship.
from hunt.database.query_builder import _EAGER_MODE, _MISSING, QueryBuilder, _run_sync
from hunt.database.query_builder import relation as relation
from hunt.support.str import Str

_JSON_CASTS = ("json", "array")


class MassAssignmentError(Exception):
    """Raised when ``fill()`` / ``create()`` is given a key on a model that allows none.

    A model is mass-assignable only for the columns it lists in ``fillable``
    (or, with ``guarded = [...]``, every column except the listed ones). A model
    that declares neither is totally guarded.
    """


class ModelMeta(type):
    """Metaclass that injects table name and sets up the class."""

    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> type:
        cls = super().__new__(mcs, name, bases, namespace)
        is_root = not any(isinstance(base, ModelMeta) for base in bases)
        if not is_root and "table" not in namespace:
            cls.table = Str.plural(Str.snake(name)).lower()
        # Each subclass gets its own event listener dict so observers don't bleed across models
        if "_event_listeners" not in namespace:
            cls._event_listeners = {}
        # Each subclass gets its own global scope dict, seeded with the scopes
        # of its bases: a subclass of a tenant-scoped model stays tenant-scoped.
        if "_global_scopes" not in namespace:
            inherited: dict = {}
            for base in reversed(cls.__mro__[1:]):
                inherited.update(vars(base).get("_global_scopes") or {})
            cls._global_scopes = inherited
        # Run boot() for every concrete model whose hierarchy (including plain
        # mixins) defines one, so scopes and observers registered there apply
        # to subclasses too.
        if not is_root and _defines_boot(cls):
            cls.boot()
        return cls


def _defines_boot(cls: type) -> bool:
    """True when any class in the MRO other than the root Model (and object) defines boot()."""
    for klass in cls.__mro__:
        if klass is object:
            continue
        if isinstance(klass, ModelMeta) and not any(isinstance(b, ModelMeta) for b in klass.__bases__):
            continue  # the root Model: its boot() is the documented no-op
        if "boot" in vars(klass):
            return True
    return False


class Model(metaclass=ModelMeta):
    # ------------------------------------------------------------------
    # Class-level configuration
    # ------------------------------------------------------------------
    table: ClassVar[str] = ""
    primary_key: ClassVar[str] = "id"
    # Mass assignment (fill / create / update_or_create ...) is allow-list based:
    #   fillable = ["title", "body"]   only these keys are accepted
    #   guarded = ["is_admin"]         every key except these (and the primary key)
    #   guarded = []                   every key except the primary key
    # With neither declared the model is totally guarded and fill() raises
    # MassAssignmentError. force_fill() and attribute assignment are never guarded.
    fillable: ClassVar[list[str]] = []
    guarded: ClassVar[list[str]] = ["*"]
    hidden: ClassVar[list[str]] = []
    appends: ClassVar[list[str]] = []
    casts: ClassVar[dict[str, str]] = {}
    timestamps: ClassVar[bool] = True
    attributes: ClassVar[dict] = {}
    _soft_deletes: ClassVar[bool] = False
    _connection: ClassVar[str | None] = None
    _global_scopes: ClassVar[dict] = {}
    route_key_name: ClassVar[str] = ""

    _event_listeners: ClassVar[dict[str, list]] = {}

    def __init__(self, attributes: dict | None = None) -> None:
        self._attributes: dict[str, Any] = dict(type(self).attributes)
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
        accessor = _find_in_mro(type(self), f"get_{name}_attribute")
        if accessor is not None:
            return accessor(self)
        if name in self.__dict__.get("_attributes", {}):
            return self._cast(name, self._attributes[name])
        if name in self.__dict__.get("_relations", {}):
            return self._relations[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
            return
        # A name that is already a loaded column is stored as a column even
        # when a class attribute has the same name (a ``table`` or ``hidden``
        # column, say). Read such a column with get_attribute().
        is_column = name in self.__dict__.get("_attributes", {})
        if not is_column and any(name in klass.__dict__ for klass in type(self).__mro__):
            super().__setattr__(name, value)
            return
        # Mutator pattern: set_<name>_attribute
        mutator = _find_in_mro(type(self), f"set_{name}_attribute")
        if mutator is not None:
            mutator(self, value)
            return
        self._attributes[name] = value

    def get_attribute(self, key: str, default: Any = None) -> Any:
        """Return a column value (cast), even when a class attribute shadows its name."""
        if key in self._attributes:
            return self._cast(key, self._attributes[key])
        return default

    def set_attribute(self, key: str, value: Any) -> Model:
        """Set a column value, even when a class attribute shadows its name."""
        self._attributes[key] = value
        return self

    def __repr__(self) -> str:
        hidden = set(self.hidden)
        visible = {k: v for k, v in self.__dict__.get("_attributes", {}).items() if k not in hidden}
        return f"{type(self).__name__}({visible!r})"

    # ------------------------------------------------------------------
    # Fill
    # ------------------------------------------------------------------

    def fill(self, attributes: dict) -> Model:
        """Mass-assign *attributes*, keeping only the keys the model allows.

        Keys outside ``fillable`` (or inside ``guarded``) are dropped. On a
        totally guarded model (no ``fillable``, default ``guarded``) nothing is
        assignable, so a non-empty fill raises ``MassAssignmentError`` instead
        of silently saving an empty row.
        """
        totally_guarded = self._totally_guarded()
        for key, value in attributes.items():
            if self._is_fillable(key):
                self._attributes[key] = value
            elif totally_guarded:
                raise MassAssignmentError(
                    f"Cannot mass-assign {key!r} on {type(self).__name__}: the model declares neither "
                    "`fillable` nor `guarded`. List the assignable columns in `fillable` "
                    "(or set `guarded = []` to allow all), or use force_fill() for trusted data."
                )
        return self

    def force_fill(self, attributes: dict) -> Model:
        self._attributes.update(attributes)
        return self

    def _totally_guarded(self) -> bool:
        return not self.fillable and "*" in self.guarded

    def _is_fillable(self, key: str) -> bool:
        # The primary key is mass-assignable only when `fillable` names it
        # explicitly (models whose key is supplied by the application, such as
        # a code or UUID). `guarded = []` alone never opens it up.
        if key == self.primary_key:
            return key in self.fillable
        if self.fillable:
            return key in self.fillable
        if "*" in self.guarded:
            return False
        return key not in self.guarded

    def update(self, attributes: dict | None = None, **kwargs: Any) -> bool:
        """Set the given attributes (bypassing mass-assignment guards) and save."""
        self.force_fill({**(attributes or {}), **kwargs})
        return self.save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> bool:
        if self.__dict__.get("timestamps", type(self).timestamps):
            now = int(time.time())
            if not self._exists:
                self._attributes.setdefault("created_at", now)
            self._attributes["updated_at"] = now

        if self._exists:
            self._fire_event("updating")
            dirty = self._dirty()
            if dirty:
                if self._instance_query().update(dirty) == 0:
                    # The row is gone (deleted elsewhere): nothing was saved.
                    return False
            self._fire_event("updated")
        else:
            self._fire_event("creating")
            pk_name = self.primary_key
            new_id = self.query().insert(self._db_values(self._attributes), returning=pk_name)
            # Keep a key the caller supplied (UUID, natural key); only adopt
            # the database-generated one when there was none.
            if self._attributes.get(pk_name) is None:
                self._attributes[pk_name] = new_id
            self._exists = True
            self._fire_event("created")

        self._original = self._db_values(self._attributes)
        return True

    def _instance_query(self) -> QueryBuilder:
        """A query for exactly this row: by its stored primary key, ignoring
        soft-delete visibility and global scopes (the instance is already loaded)."""
        pk_name = self.primary_key
        key = self._original[pk_name] if pk_name in self._original else self._attributes[pk_name]
        return self.query().with_trashed().without_global_scopes().where(pk_name, key)

    def delete(self) -> bool:
        if not self._exists:
            return False
        self._fire_event("deleting")
        if self._soft_deletes:
            now = int(time.time())
            if self._instance_query().update({"deleted_at": now}) == 0:
                return False
            self._attributes["deleted_at"] = now
            self._original["deleted_at"] = now
        else:
            if self._instance_query().force_delete() == 0:
                self._exists = False
                return False
            self._exists = False
        self._fire_event("deleted")
        return True

    def restore(self) -> bool:
        if not self._soft_deletes:
            return False
        if self._instance_query().update({"deleted_at": None}) == 0:
            return False
        self._attributes["deleted_at"] = None
        self._original["deleted_at"] = None
        return True

    def force_delete(self) -> bool:
        """Permanently delete this row, including a soft-deleted one."""
        if not self._exists:
            return False
        deleted = self._instance_query().force_delete()
        self._exists = False
        return deleted > 0

    def replicate(self, except_: list[str] | None = None) -> Model:
        """Return an unsaved copy of this model without the primary key."""
        exclude = set(except_ or []) | {self.primary_key}
        attrs = {k: v for k, v in self._attributes.items() if k not in exclude}
        instance = type(self).__new__(type(self))
        instance._attributes = attrs
        instance._original = {}
        instance._exists = False
        instance._relations = {}
        return instance

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _dirty(self) -> dict:
        dirty = {}
        for k, v in self._attributes.items():
            stored = self._db_value(k, v)
            if k not in self._original or self._original[k] != stored:
                dirty[k] = stored
        return dirty

    def _db_value(self, key: str, value: Any) -> Any:
        """The value as written to the database: ``json`` / ``array`` casts are serialised."""
        if value is not None and not isinstance(value, (str, bytes)) and self.casts.get(key) in _JSON_CASTS:
            return json.dumps(value)
        return value

    def _db_values(self, attributes: dict) -> dict:
        return {k: self._db_value(k, v) for k, v in attributes.items()}

    def is_dirty(self, *keys: str) -> bool:
        dirty = self._dirty()
        if not keys:
            return bool(dirty)
        return any(k in dirty for k in keys)

    # ------------------------------------------------------------------
    # Class-level query methods
    # ------------------------------------------------------------------

    @classmethod
    def boot(cls) -> None:
        """Override to register global scopes or observers at class definition time."""

    @classmethod
    def add_global_scope(cls, name: str, scope: Callable[[QueryBuilder], QueryBuilder]) -> None:
        cls._global_scopes[name] = scope

    @classmethod
    def without_global_scope(cls, *names: str) -> QueryBuilder:
        return cls.query().without_global_scope(*names)

    @classmethod
    def without_global_scopes(cls) -> QueryBuilder:
        return cls.query().without_global_scopes()

    @classmethod
    def resolve_route_binding(cls, value: Any) -> Model:
        key = cls.route_key_name or cls.primary_key
        instance = cls.query().where(key, value).first()
        if instance is None:
            raise ValueError(f"{cls.__name__} not found with {key}={value!r}")
        return instance

    @classmethod
    def query(cls) -> QueryBuilder:
        qb = QueryBuilder(cls.table, cls, cls._connection)
        qb._global_scope_fns = dict(cls._global_scopes)
        return qb

    @classmethod
    def with_(cls, *relations: str | dict) -> QueryBuilder:
        """Start a query with eager-loaded relations."""
        return cls.query().with_(*relations)

    @classmethod
    def all(cls) -> QueryBuilder:
        return cls.query()

    @classmethod
    def order_by(cls, column: str, direction: str = "asc") -> QueryBuilder:
        return cls.query().order_by(column, direction)

    @classmethod
    def latest(cls, column: str = "created_at") -> QueryBuilder:
        return cls.query().latest(column)

    @classmethod
    def oldest(cls, column: str = "created_at") -> QueryBuilder:
        return cls.query().oldest(column)

    @classmethod
    def limit(cls, n: int) -> QueryBuilder:
        return cls.query().limit(n)

    @classmethod
    def select(cls, *columns: str) -> QueryBuilder:
        return cls.query().select(*columns)

    @classmethod
    def where_raw(cls, sql: str, bindings: list | dict | None = None) -> QueryBuilder:
        return cls.query().where_raw(sql, bindings)

    @classmethod
    def find(cls, id: Any) -> Model | None:
        return cls.query().find(id)

    @classmethod
    def find_or_fail(cls, id: Any) -> Model:
        instance = cls.find(id)
        if instance is None:
            from hunt.http.response import HttpException

            raise HttpException(404, f"{cls.__name__} not found.")
        return instance

    @classmethod
    def where(cls, column: str, operator_or_value: Any = _MISSING, value: Any = _MISSING) -> QueryBuilder:
        return cls.query().where(column, operator_or_value, value)

    @classmethod
    def where_like(cls, column: str, term: Any) -> QueryBuilder:
        return cls.query().where_like(column, term)

    @classmethod
    def where_in(cls, column: str, values: list) -> QueryBuilder:
        return cls.query().where_in(column, values)

    @classmethod
    def where_not_in(cls, column: str, values: list) -> QueryBuilder:
        return cls.query().where_not_in(column, values)

    @classmethod
    def or_where(cls, column: str, operator_or_value: Any = _MISSING, value: Any = _MISSING) -> QueryBuilder:
        return cls.query().or_where(column, operator_or_value, value)

    @classmethod
    def where_null(cls, column: str) -> QueryBuilder:
        return cls.query().where_null(column)

    @classmethod
    def where_not_null(cls, column: str) -> QueryBuilder:
        return cls.query().where_not_null(column)

    @classmethod
    def where_group(cls, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        return cls.query().where_group(callback)

    @classmethod
    def or_where_group(cls, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        return cls.query().or_where_group(callback)

    @classmethod
    def create(cls, data: dict | None = None, **kwargs: Any) -> Model:
        attrs = {**(data or {}), **kwargs}
        instance = cls(attrs)
        instance.save()
        return instance

    @classmethod
    def first_or_create(cls, search: dict, attributes: dict | None = None) -> tuple[Model, bool]:
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            return existing, False
        merged = {**search, **(attributes or {})}
        return cls.create(merged), True

    @classmethod
    def update_or_create(cls, search: dict, values: dict) -> Model:
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            existing.fill(values)
            existing.save()
            return existing
        return cls.create({**search, **values})

    @classmethod
    def first_or_new(cls, search: dict, attributes: dict | None = None) -> tuple[Model, bool]:
        """Like first_or_create but does not persist the new instance."""
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            return existing, False
        return cls({**search, **(attributes or {})}), True

    @classmethod
    def create_many(cls, rows: list[dict]) -> None:
        """Bulk-insert records in a single query. Model events are not fired."""
        now = int(time.time())
        use_timestamps = cls.timestamps
        prepared = []
        for attrs in rows:
            instance = cls()
            instance.fill(attrs)
            if use_timestamps:
                instance._attributes.setdefault("created_at", now)
                instance._attributes["updated_at"] = now
            prepared.append(instance._db_values(instance._attributes))
        cls.query().insert_many(prepared)

    def increment(self, column: str, amount: int = 1) -> bool:
        if self._instance_query().increment(column, amount) == 0:
            return False
        self._attributes[column] = (self._attributes.get(column) or 0) + amount
        self._original[column] = self._attributes[column]
        return True

    def decrement(self, column: str, amount: int = 1) -> bool:
        if self._instance_query().decrement(column, amount) == 0:
            return False
        self._attributes[column] = (self._attributes.get(column) or 0) - amount
        self._original[column] = self._attributes[column]
        return True

    # ------------------------------------------------------------------
    # Async persistence — run sync ORM calls in the thread-pool executor so
    # async controller actions do not block the ASGI event loop.
    # ------------------------------------------------------------------

    async def async_save(self) -> bool:
        return await _run_sync(self.save)

    async def async_delete(self) -> bool:
        return await _run_sync(self.delete)

    async def async_restore(self) -> bool:
        return await _run_sync(self.restore)

    @classmethod
    async def async_find(cls, id: Any) -> Model | None:
        return await _run_sync(cls.find, id)

    @classmethod
    async def async_find_or_fail(cls, id: Any) -> Model:
        return await _run_sync(cls.find_or_fail, id)

    @classmethod
    async def async_create(cls, data: dict | None = None, **kwargs: Any) -> Model:
        return await _run_sync(cls.create, data, **kwargs)

    @classmethod
    async def async_first_or_create(cls, search: dict, attributes: dict | None = None) -> tuple[Model, bool]:
        return await _run_sync(cls.first_or_create, search, attributes)

    @classmethod
    async def async_update_or_create(cls, search: dict, values: dict) -> Model:
        return await _run_sync(cls.update_or_create, search, values)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def has_one(self, related: type, foreign_key: str | None = None, local_key: str | None = None) -> Any:
        from hunt.database.relations.has_one import HasOne

        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        lk = local_key or self.primary_key
        rel = HasOne(related, self, fk, lk)
        if _EAGER_MODE.get():
            return rel
        return rel.get_result()

    def has_many(self, related: type, foreign_key: str | None = None, local_key: str | None = None) -> Any:
        from hunt.database.relations.has_many import HasMany

        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        lk = local_key or self.primary_key
        rel = HasMany(related, self, fk, lk)
        if _EAGER_MODE.get():
            return rel
        return rel.get_results()

    def belongs_to(self, related: type, foreign_key: str | None = None, owner_key: str | None = None) -> Any:
        from hunt.database.relations.belongs_to import BelongsTo

        fk = foreign_key or f"{Str.snake(related.__name__)}_id"
        ok = owner_key or getattr(related, "primary_key", None) or "id"
        rel = BelongsTo(related, self, fk, ok)
        if _EAGER_MODE.get():
            return rel
        return rel.get_result()

    def belongs_to_many(
        self,
        related: type,
        pivot_table: str | None = None,
        foreign_key: str | None = None,
        related_key: str | None = None,
    ) -> Any:
        from hunt.database.relations.belongs_to_many import BelongsToMany

        pivot = pivot_table or "_".join(
            sorted(
                [
                    Str.snake(type(self).__name__),
                    Str.snake(related.__name__),
                ]
            )
        )
        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        rk = related_key or f"{Str.snake(related.__name__)}_id"
        rel = BelongsToMany(related, self, pivot, fk, rk)
        if _EAGER_MODE.get():
            return rel
        return rel.get_results()

    def related(self, name: str) -> Any:
        """Return the related model(s) for relationship *name*, loading them at most once.

        After ``with_("author")`` this returns the eager-loaded value without a
        query. Otherwise the relationship is queried on first use and the
        result is kept on the instance::

            for post in Post.query().with_("author").get():
                post.related("author").name      # no extra query
        """
        if name not in self._relations:
            relation = self.get_relation(name)  # also rejects names that are not relationships
            loader = getattr(relation, "get_results", None) or relation.get_result
            self._relations[name] = loader()
        return self._relations[name]

    def get_relation(self, name: str) -> Any:
        """Return the Relation object (HasMany, BelongsToMany, ...) behind a relationship method.

        Only genuine relationship methods are accepted (the same rules as
        ``with_()``); anything else raises ``ValueError`` and no query runs.
        Use it for ``attach`` / ``detach`` / ``sync`` or to inspect a relation::

            post.get_relation("tags").sync([1, 2, 3])
        """
        from hunt.database.query_builder import _resolve_relation

        return _resolve_relation(self, name)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        data = {k: self._cast(k, v) for k, v in self._attributes.items() if k not in self.hidden}
        # Appended accessors
        for attr in self.appends:
            accessor = _find_in_mro(type(self), f"get_{attr}_attribute")
            if accessor is not None:
                data[attr] = accessor(self)
        # Loaded relations
        for rel_name, rel_value in self.__dict__.get("_relations", {}).items():
            if isinstance(rel_value, Model):
                data[rel_name] = rel_value.to_dict()
            elif isinstance(rel_value, list):
                data[rel_name] = [r.to_dict() if isinstance(r, Model) else r for r in rel_value]
            else:
                data[rel_name] = rel_value
        return data

    def to_json(self) -> str:
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
        # Listeners registered on a parent model also fire for its subclasses.
        # A subclass re-runs an inherited boot(), which registers the same
        # observer again on the subclass: such a duplicate fires once.
        seen: set = set()
        for klass in type(self).__mro__:
            listeners = vars(klass).get("_event_listeners")
            if not listeners:
                continue
            own_keys = []
            for cb in listeners.get(f"{klass.__name__}.{event}", []):
                key = _listener_key(cb)
                own_keys.append(key)
                if key in seen:
                    continue
                cb(self)
            seen.update(own_keys)

    # ------------------------------------------------------------------
    # Casting
    # ------------------------------------------------------------------

    def _cast(self, key: str, value: Any) -> Any:
        cast_type = self.casts.get(key)
        if cast_type is None or value is None:
            return value
        if cast_type in ("int", "integer"):
            return int(value)
        if cast_type == "float":
            return float(value)
        if cast_type in ("bool", "boolean"):
            return bool(value)
        if cast_type in ("str", "string"):
            return str(value)
        if cast_type in _JSON_CASTS:
            return json.loads(value) if isinstance(value, str) else value
        return value


def _find_in_mro(cls: type, name: str) -> Any:
    """Look a plain attribute up through the class hierarchy (accessors and mutators are inherited)."""
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass.__dict__[name]
    return None


def _listener_key(cb: Any) -> Any:
    """Identify "the same listener" across two registrations made by an inherited boot()."""
    func = getattr(cb, "__func__", None)
    if func is not None and hasattr(cb, "__self__"):
        return (getattr(func, "__code__", func), type(cb.__self__))
    return getattr(cb, "__code__", None) or id(cb)

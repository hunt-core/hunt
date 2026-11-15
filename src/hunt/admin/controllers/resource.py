from __future__ import annotations

import logging
from typing import Any

from hunt.admin.authorization import allowed as _can
from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response
from hunt.support.str import Str
from hunt.validation.validator import Validator

_log = logging.getLogger(__name__)


def _eager_load_belongs_to(fields: list, items: list) -> None:
    """Batch-load BelongsTo FK relations to avoid one query per row on the index."""
    from hunt.admin.fields.belongs_to import BelongsTo

    for field in fields:
        if not isinstance(field, BelongsTo):
            continue
        try:
            related_resource = field.related_resource_class()
            fk_values = list(
                {
                    item._attributes.get(field.attribute)
                    for item in items
                    if item._attributes.get(field.attribute) is not None
                }
            )
            if not fk_values:
                continue
            related_items = related_resource.model.query().where_in("id", fk_values).get()
            related_map = {str(r._attributes.get("id")): related_resource.title(r) for r in related_items}
            for item in items:
                if not hasattr(item, "_relation_cache"):
                    item._relation_cache = {}
                fk = item._attributes.get(field.attribute)
                if fk is not None:
                    item._relation_cache[field.attribute] = related_map.get(str(fk), str(fk))
        except Exception:
            continue


def _current_user_id() -> Any:
    try:
        from hunt.auth.manager import Auth

        user = Auth.user()
        return user._attributes.get("id") if user is not None else None
    except Exception:
        return None


def _get_resource(resource_key: str) -> Any:
    from hunt.admin.application import Admin

    resource_cls = Admin.find_resource(resource_key)
    if resource_cls is None:
        raise HttpException(404, "Resource not found.")
    return resource_cls()


def _get_instance(resource: Any, id: str, request: Any = None) -> Any:
    try:
        instance = resource.model.find(id)
    except Exception:
        instance = None
    if instance is None:
        raise HttpException(404, "Record not found.")
    if request is not None and not _in_index_scope(resource, instance, request):
        raise HttpException(404, "Record not found.")
    return instance


def _in_index_scope(resource: Any, instance: Any, request: Any) -> bool:
    """True when the record is one the resource's ``index_query`` would list.

    A resource that narrows ``index_query`` (to a tenant or an owner) narrows
    the single-record pages the same way: a record outside it is a 404.
    """
    try:
        primary_key = getattr(resource.model, "primary_key", "id")
        value = instance._attributes.get(primary_key)
        return bool(resource.index_query(request).where(primary_key, value).exists())
    except Exception:
        _log.exception("Admin: could not check whether record is inside index_query; treating it as not found")
        return False


def _flash_and_redirect(request: Request, key: str, message: str, url: str) -> RedirectResponse:
    store = getattr(request, "_session", None)
    if store is not None:
        store.flash(key, message)
    return RedirectResponse(url)


def _primary_key(model: Any) -> str:
    pk = getattr(model, "primary_key", "id")
    return pk if isinstance(pk, str) and pk else "id"


def _audit(resource: Any, record_id: Any, action: str, old: dict, new: dict) -> None:
    """Write an audit row when the resource opted in. Values must already be sanitised."""
    from hunt.admin.audit import AuditLog, _write_audit

    if isinstance(resource, AuditLog):
        _write_audit(_current_user_id(), type(resource).__name__, record_id, action, old, new)


def _in_transaction(model: Any, fn: Any) -> Any:
    """Run ``fn`` inside a database transaction on the model's connection."""
    from hunt.database.connection import transaction

    conn_name = getattr(model, "_connection", None)
    if not isinstance(conn_name, str):
        conn_name = None
    return transaction(lambda _conn: fn(), conn_name)


def _page_meta(result: Any, key: str) -> Any:
    try:
        return result[key]
    except Exception:
        return getattr(result, key, None)


def _base_query(resource: Any, request: Request, per_page: int) -> dict:
    """Query parameters that every index link (sort, pagination) must carry."""
    from hunt.admin.filter import DateRangeFilter

    params: dict[str, Any] = {}
    search = request.query("search", "")
    if search:
        params["search"] = search
    try:
        filters = resource.filters()
    except Exception:
        filters = []
    for f in filters:
        slug = f.slug()
        names = (f"filter_{slug}_from", f"filter_{slug}_to") if isinstance(f, DateRangeFilter) else (f"filter_{slug}",)
        for name in names:
            value = request.query(name, None)
            if value not in (None, ""):
                params[name] = value
    sort = request.query("sort", "")
    if sort and sort in resource._sortable_columns():
        params["sort"] = sort
        direction = request.query("dir", "desc")
        params["dir"] = direction if direction in ("asc", "desc") else "desc"
    params["per_page"] = per_page
    return params


def _visible_has_many_panels(resource: Any, request: Request) -> list:
    """HasMany panels whose related resource the user is allowed to view."""
    from hunt.admin.fields.has_many import HasMany

    panels = []
    for field in resource.fields():
        if not isinstance(field, HasMany):
            continue
        try:
            related = field.related_resource_class()
        except Exception:
            _log.exception("HasMany panel %r: could not build the related resource", field.attribute)
            continue
        if _can(related, "can_view_any", request):
            panels.append(field)
    return panels


def _has_many_query(panel: Any, instance: Any) -> tuple[Any, Any]:
    """Return (related resource instance, query scoped to the parent record)."""
    related = panel.related_resource_class()
    fk = panel.foreign_key or f"{Str.snake(type(instance).__name__)}_id"
    parent_pk = _primary_key(type(instance))
    return related, related.model.query().where(fk, instance._attributes.get(parent_pk))


def index(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not _can(resource, "can_view_any", request):
        raise HttpException(403, "Forbidden.")

    try:
        page = max(1, int(request.query("page", "1")))
    except (ValueError, TypeError):
        page = 1

    # The resource's own per_page is always a valid choice, even when it is not
    # one of the stock options (the stubs generate 15 and 20).
    _options = list(resource.per_page_options or [10, 25, 50, 100])
    configured = resource.per_page
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        if configured not in _options:
            _options = sorted({*_options, configured})
        _default = configured
    else:
        _default = _options[0]
    try:
        per_page = int(request.query("per_page", str(_default)))
        if per_page not in _options:
            per_page = _default
    except (ValueError, TypeError):
        per_page = _default

    query = resource.index_query(request)
    paginate_result = query.paginate(per_page, page)
    last_page = _page_meta(paginate_result, "last_page")
    if isinstance(last_page, int) and not isinstance(last_page, bool) and page > max(1, last_page):
        # Past the end (stale link, rows deleted): show the last page instead of an empty one.
        page = max(1, last_page)
        paginate_result = query.paginate(per_page, page)

    index_fields = [f for f in resource.fields() if f._show_on_index]
    items = paginate_result["data"]
    _eager_load_belongs_to(index_fields, items)

    sort = request.query("sort", "")
    if sort not in resource._sortable_columns():
        sort = ""
    direction = request.query("dir", "desc")
    if direction not in ("asc", "desc"):
        direction = "desc"

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": resource.get_label_plural(),
            "resource": resource,
            "resource_key": resource_key,
            "items": items,
            "pagination": paginate_result,
            "fields": index_fields,
            "search": request.query("search", ""),
            "sort": sort,
            "dir": direction,
            "filters": resource.filters(),
            "actions": resource.actions(),
            "metrics": [],
            "per_page": per_page,
            "per_page_options": _options,
            "base_query": _base_query(resource, request, per_page),
            "can_create": _can(resource, "can_create", request),
            "can_update": _can(resource, "can_update", request),
            "can_delete": _can(resource, "can_delete", request),
        }
    )

    # Calculate per-resource metrics
    for card in resource.metrics():
        try:
            data = card.calculate()
            data["metric_type"] = card.metric_type
            ctx["metrics"].append(data)
        except Exception:
            pass

    return Admin._render("admin/resource/index.html", ctx)


def _toggleable_attributes(resource: Any) -> set[str]:
    """Boolean attributes that may be flipped inline: editable ones only."""
    from hunt.admin.fields.boolean import Boolean

    return {
        f.attribute
        for f in resource.fields()
        if isinstance(f, Boolean) and f._readonly is not True and f._show_on_edit is True
    }


def toggle_field(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.audit import audit_safe
    from hunt.http.response import JsonResponse

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id, request)
    if not _can(resource, "can_update", request, instance):
        raise HttpException(403, "Forbidden.")

    attribute = str(request.input("attribute") or "").strip()
    if not attribute:
        raise HttpException(400, "Missing attribute.")

    if attribute not in _toggleable_attributes(resource):
        raise HttpException(400, "Not a toggleable boolean field.")

    old_attrs = audit_safe(resource, instance._attributes)
    new_value = not bool(instance._attributes.get(attribute))
    # The declared field list is the allow-list, so write past $fillable:
    # fill() would silently drop a guarded column and report a change that
    # never happened.
    instance.force_fill({attribute: new_value})
    instance.save()
    if bool(instance._attributes.get(attribute)) is not new_value:
        raise HttpException(422, "The field could not be updated.")

    _audit(resource, id, "toggle", old_attrs, audit_safe(resource, instance._attributes))

    return JsonResponse({"value": new_value})


def show(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not _can(resource, "can_view_any", request):
        raise HttpException(403, "Forbidden.")

    instance = _get_instance(resource, id, request)
    detail_fields = [f for f in resource.fields() if f._show_on_detail]
    _eager_load_belongs_to(detail_fields, [instance])
    has_many_panels = _visible_has_many_panels(resource, request)

    related_data = {}
    for panel in has_many_panels:
        rel_attr = panel.attribute
        try:
            related_resource_inst, rel_query = _has_many_query(panel, instance)
            rel_sort = request.query(f"rel_{rel_attr}_sort", "")
            rel_dir = request.query(f"rel_{rel_attr}_dir", "asc")
            rel_search = request.query(f"rel_{rel_attr}_search", "")
            if rel_dir not in ("asc", "desc"):
                rel_dir = "asc"
            if rel_search:
                # Grouped, so the search cannot escape the parent foreign key.
                rel_query = related_resource_inst._search_group(rel_query, rel_search)
            sortable_cols = related_resource_inst._sortable_columns()
            if rel_sort and rel_sort in sortable_cols:
                rel_query = rel_query.order_by(rel_sort, rel_dir)
            else:
                rel_sort = ""
                rel_query = rel_query.order_by(_primary_key(related_resource_inst.model), "asc")
            related_data[rel_attr] = {
                "items": rel_query.limit(100).get(),
                "sort": rel_sort,
                "dir": rel_dir,
                "search": rel_search,
            }
        except Exception:
            _log.exception("HasMany panel %r could not be loaded", rel_attr)
            related_data[rel_attr] = {"items": [], "sort": "", "dir": "asc", "search": ""}

    from hunt.admin.audit import AuditLog, _read_audit

    audit_history: list[dict] = []
    if isinstance(resource, AuditLog):
        audit_history = _read_audit(type(resource).__name__, id)

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": resource.title(instance),
            "resource": resource,
            "resource_key": resource_key,
            "instance": instance,
            "fields": detail_fields,
            "has_many_panels": has_many_panels,
            "related_data": related_data,
            "record_id": id,
            "can_update": _can(resource, "can_update", request, instance),
            "can_delete": _can(resource, "can_delete", request, instance),
            "audit_history": audit_history,
        }
    )
    return Admin._render("admin/resource/show.html", ctx)


def create(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not _can(resource, "can_create", request):
        raise HttpException(403, "Forbidden.")

    create_fields = [f for f in resource.fields() if f._show_on_create]
    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": f"Create {resource.get_label()}",
            "resource": resource,
            "resource_key": resource_key,
            "fields": create_fields,
        }
    )
    return Admin._render("admin/resource/create.html", ctx)


def _collect_data(request: Request, fields: list) -> dict:
    """Merge form text values with uploaded files for the given field list."""
    from hunt.admin.fields.belongs_to import BelongsTo as BelongsToField
    from hunt.admin.fields.belongs_to_many import BelongsToMany as BelongsToManyField
    from hunt.admin.fields.boolean import Boolean as BooleanField
    from hunt.admin.fields.image import Image as ImageField
    from hunt.validation.validator import ValidationException

    # BelongsToMany fields are pivot-only — synced separately after save, not via fill()
    allowed_attrs = {f.attribute for f in fields if not f._readonly and not isinstance(f, BelongsToManyField)}
    raw_data = request.all()
    data = {k: v for k, v in raw_data.items() if k in allowed_attrs}

    # Convert empty-string FK values to None so the DB receives NULL, not ''
    for field in fields:
        if isinstance(field, BelongsToField) and not field._readonly:
            if field.attribute in data and data[field.attribute] == "":
                data[field.attribute] = None

    # Unchecked checkboxes are absent from POST data entirely. Explicitly set
    # them to False so the model sees the unchecked state.
    for field in fields:
        if isinstance(field, BooleanField) and not field._readonly and field.attribute not in data:
            data[field.attribute] = False
    size_errors: dict[str, list[str]] = {}
    for field in fields:
        if isinstance(field, ImageField) and not field._readonly:
            uploaded = request.file(field.attribute)
            if uploaded is not None and uploaded.size > 0:
                if uploaded.size > field._max_kb * 1024:
                    size_errors[field.attribute] = [f"The file may not be greater than {field._max_kb} KB."]
                else:
                    data[field.attribute] = uploaded
    if size_errors:
        raise ValidationException(size_errors)
    return data


def _store_image_fields(data: dict, fields: list) -> dict:
    """Replace UploadedFile values with stored paths; skip if no file submitted."""
    from hunt.admin.fields.image import Image as ImageField
    from hunt.http.request import UploadedFile

    result = {}
    field_map = {f.attribute: f for f in fields if not f._readonly}
    for key, value in data.items():
        if isinstance(value, UploadedFile):
            field = field_map.get(key)
            disk = field._disk if field and isinstance(field, ImageField) else "public"
            path = field._path if field and isinstance(field, ImageField) else "uploads"
            result[key] = value.store(path, disk)
        else:
            result[key] = value
    return result


def _prepare_for_storage(data: dict, fields: list, instance: Any = None) -> dict:
    """Convert validated form values to storage values.

    Runs after validation (rules see what the user typed) and after image
    storage. Each field converts its value with ``from_form_value`` and then
    ``prepare_for_storage``; a field returning ``SKIP`` is left out, so the
    column keeps its current value (a blank password on edit).
    """
    from hunt.admin.field import SKIP
    from hunt.http.request import UploadedFile

    is_update = instance is not None
    current_attrs = instance._attributes if is_update else {}
    field_map = {f.attribute: f for f in fields if not f._readonly}
    result: dict = {}
    for key, value in data.items():
        field = field_map.get(key)
        # Only declared, writable fields are stored; an upload that was not
        # turned into a path by _store_image_fields is never written.
        if field is None or isinstance(value, UploadedFile):
            continue
        value = field.from_form_value(value, current_attrs.get(key))
        value = field.prepare_for_storage(value, is_update=is_update)
        if value is SKIP:
            continue
        result[key] = value
    return result


def _parse_ids(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = str(raw if raw is not None else "").split(",")
    seen: list[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def _resolve_belongs_to_many(request: Request, fields: list) -> list[tuple[Any, list]]:
    """Validate submitted BelongsToMany ids. Returns [(field, [related primary keys])].

    A field whose input is absent from the request is skipped entirely (its
    pivots are left alone). Ids are never coerced to ``int``: they are matched
    against the related model, which also yields the correctly typed keys.
    """
    from hunt.admin.fields.belongs_to_many import BelongsToMany as BelongsToManyField
    from hunt.validation.validator import ValidationException

    submitted = request.all()
    plan: list[tuple[Any, list]] = []
    errors: dict[str, list[str]] = {}
    for field in fields:
        if not isinstance(field, BelongsToManyField) or field._readonly:
            continue
        if field.attribute not in submitted:
            continue
        ids = _parse_ids(submitted.get(field.attribute))
        keys: list = []
        if ids:
            try:
                related_model = field.related_resource_class().model
                pk = _primary_key(related_model)
                found = {
                    str(m._attributes.get(pk)): m._attributes.get(pk)
                    for m in related_model.query().where_in(pk, ids).get()
                }
            except Exception:
                _log.exception("BelongsToMany %r: could not validate the selected records", field.attribute)
                found = {}
            if any(i not in found for i in ids):
                errors[field.attribute] = [f"The selected {field.label.lower()} is invalid."]
                continue
            keys = [found[i] for i in ids]
        plan.append((field, keys))
    if errors:
        raise ValidationException(errors)
    return plan


def _apply_belongs_to_many(instance: Any, plan: list[tuple[Any, list]]) -> dict[str, dict]:
    """Apply the pivot diff for each planned field. Returns {attribute: {"old": [...], "new": [...]}}.

    Only the difference is written (no detach-all), and the caller runs this
    inside a transaction so a failure cannot leave the record without pivots.
    """
    from hunt.admin.fields.belongs_to_many import relation_object
    from hunt.validation.validator import ValidationException

    changes: dict[str, dict] = {}
    for field, wanted in plan:
        try:
            rel = relation_object(instance, field.relation_method)
            related_pk = _primary_key(field.related_resource_class().model)
            current = {str(m._attributes.get(related_pk)): m._attributes.get(related_pk) for m in rel.get_results()}
            wanted_map = {str(k): k for k in wanted}
            to_detach = [v for k, v in current.items() if k not in wanted_map]
            to_attach = [v for k, v in wanted_map.items() if k not in current]
            for key in to_detach:
                rel.detach(key)
            for key in to_attach:
                rel.attach(key)
        except Exception as exc:
            _log.exception("BelongsToMany %r: pivot sync failed", field.attribute)
            raise ValidationException(
                {field.attribute: [f"The {field.label.lower()} could not be saved. No changes were made."]}
            ) from exc
        if to_detach or to_attach:
            changes[field.attribute] = {"old": sorted(current), "new": sorted(wanted_map)}
    return changes


def _sync_belongs_to_many(request: Request, instance: Any, fields: list) -> dict[str, dict]:
    """Validate and atomically sync pivot rows for every BelongsToMany field in ``fields``."""
    if instance is None:
        return {}
    plan = _resolve_belongs_to_many(request, fields)
    if not plan:
        return {}
    return _in_transaction(type(instance), lambda: _apply_belongs_to_many(instance, plan))


def store(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_safe

    resource = _get_resource(resource_key)
    if not _can(resource, "can_create", request):
        raise HttpException(403, "Forbidden.")

    create_fields = [f for f in resource.fields() if f._show_on_create]
    # Each field's rules go to the validator as a list: joined with "|", a
    # regex: rule would swallow every rule after it.
    rules = {f.attribute: list(f._rules) for f in create_fields if f._rules}
    data = _collect_data(request, create_fields)

    if rules:
        Validator.make(data, rules).validate()

    pivot_plan = _resolve_belongs_to_many(request, create_fields)
    stored = _store_image_fields(data, create_fields)
    stored = _prepare_for_storage(stored, create_fields)

    # The declared, non-readonly fields are the allow-list: force_fill so a
    # declared field on a guarded / non-fillable column is actually saved.
    instance = resource.model()
    instance.force_fill(stored)

    def _persist() -> dict:
        instance.save()
        return _apply_belongs_to_many(instance, pivot_plan)

    pivot_changes = _in_transaction(resource.model, _persist)

    record_id = instance._attributes.get(_primary_key(resource.model), "")
    new_attrs = audit_safe(resource, instance._attributes)
    for attribute, change in pivot_changes.items():
        new_attrs[attribute] = change["new"]
    _audit(resource, record_id, "create", {}, new_attrs)

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} created successfully.",
        f"{Admin.prefix}/resources/{resource_key}",
    )


def edit(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id, request)
    if not _can(resource, "can_update", request, instance):
        raise HttpException(403, "Forbidden.")

    edit_fields = [f for f in resource.fields() if f._show_on_edit]
    has_many_panels = _visible_has_many_panels(resource, request)
    related_data: dict = {}
    for panel in has_many_panels:
        try:
            _related, rel_query = _has_many_query(panel, instance)
            related_data[panel.attribute] = rel_query.limit(20).get()
        except Exception:
            _log.exception("HasMany panel %r could not be loaded", panel.attribute)
            related_data[panel.attribute] = []

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": f"Edit {resource.title(instance)}",
            "resource": resource,
            "resource_key": resource_key,
            "instance": instance,
            "fields": edit_fields,
            "old": ctx["old"] or instance._attributes,
            "record_id": id,
            "has_many_panels": has_many_panels,
            "related_data": related_data,
        }
    )
    return Admin._render("admin/resource/edit.html", ctx)


def update(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_safe

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id, request)
    if not _can(resource, "can_update", request, instance):
        raise HttpException(403, "Forbidden.")

    edit_fields = [f for f in resource.fields() if f._show_on_edit]
    rules = {f.attribute: list(f._rules) for f in edit_fields if f._rules}
    data = _collect_data(request, edit_fields)

    # A blank password on edit means "keep the current one": it is neither
    # validated (so "required|min:8" does not reject it) nor written.
    for f in edit_fields:
        if getattr(f, "keep_when_blank_on_update", False) is True and data.get(f.attribute) in (None, ""):
            data.pop(f.attribute, None)
            rules.pop(f.attribute, None)

    if rules:
        Validator.make(data, rules).validate()

    pivot_plan = _resolve_belongs_to_many(request, edit_fields)
    stored = _store_image_fields(data, edit_fields)
    final = _prepare_for_storage(stored, edit_fields, instance)
    old_attrs = audit_safe(resource, instance._attributes)
    # See store(): declared fields are the allow-list, so bypass $fillable.
    instance.force_fill(final)

    def _persist() -> dict:
        instance.save()
        return _apply_belongs_to_many(instance, pivot_plan)

    pivot_changes = _in_transaction(resource.model, _persist)

    new_attrs = audit_safe(resource, instance._attributes)
    for attribute, change in pivot_changes.items():
        old_attrs[attribute] = change["old"]
        new_attrs[attribute] = change["new"]
    _audit(resource, id, "update", old_attrs, new_attrs)

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} updated successfully.",
        f"{Admin.prefix}/resources/{resource_key}/{id}",
    )


def destroy(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_safe

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id, request)
    if not _can(resource, "can_delete", request, instance):
        raise HttpException(403, "Forbidden.")

    old_attrs = audit_safe(resource, instance._attributes)
    try:
        instance.delete()
    except Exception:
        _log.exception("Admin: could not delete %s %s", type(resource).__name__, id)
        return _flash_and_redirect(
            request,
            "admin_error",
            "Could not delete record. Please try again.",
            f"{Admin.prefix}/resources/{resource_key}",
        )

    _audit(resource, id, "delete", old_attrs, {})

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} deleted successfully.",
        f"{Admin.prefix}/resources/{resource_key}",
    )

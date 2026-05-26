from __future__ import annotations

from typing import Any

from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response
from hunt.validation.validator import Validator


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


def _get_instance(resource: Any, id: str) -> Any:
    try:
        instance = resource.model.find(id)
    except Exception:
        instance = None
    if instance is None:
        raise HttpException(404, "Record not found.")
    return instance


def _flash_and_redirect(request: Request, key: str, message: str, url: str) -> RedirectResponse:
    store = getattr(request, "_session", None)
    if store is not None:
        store.flash(key, message)
    return RedirectResponse(url)


def index(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not resource.can_view_any(request):
        raise HttpException(403, "Forbidden.")

    try:
        page = int(request.query("page", "1"))
    except (ValueError, TypeError):
        page = 1

    _options = resource.per_page_options or [10, 25, 50, 100]
    _default = resource.per_page if resource.per_page in _options else _options[0]
    try:
        per_page = int(request.query("per_page", str(_default)))
        if per_page not in _options:
            per_page = _default
    except (ValueError, TypeError):
        per_page = _default

    query = resource.index_query(request)
    paginate_result = query.paginate(per_page, page)

    index_fields = [f for f in resource.fields() if f._show_on_index]
    items = paginate_result["data"]
    _eager_load_belongs_to(index_fields, items)

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
            "sort": request.query("sort", ""),
            "dir": request.query("dir", "desc"),
            "filters": resource.filters(),
            "actions": resource.actions(),
            "metrics": [],
            "per_page": per_page,
            "per_page_options": _options,
            "can_create": resource.can_create(request),
            "can_update": resource.can_update(request),
            "can_delete": resource.can_delete(request),
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


def show(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.fields.has_many import HasMany

    resource = _get_resource(resource_key)
    if not resource.can_view_any(request):
        raise HttpException(403, "Forbidden.")

    instance = _get_instance(resource, id)
    detail_fields = [f for f in resource.fields() if f._show_on_detail]
    _eager_load_belongs_to(detail_fields, [instance])
    has_many_panels = [f for f in resource.fields() if isinstance(f, HasMany)]

    related_data = {}
    for panel in has_many_panels:
        try:
            related_resource_inst = panel.related_resource_class()
            fk = panel.foreign_key or f"{type(instance).__name__.lower()}_id"
            rel_query = related_resource_inst.model.query().where(fk, instance._attributes.get("id"))
            related_data[panel.attribute] = rel_query.limit(20).get()
        except Exception:
            related_data[panel.attribute] = []

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
            "can_update": resource.can_update(request, instance),
            "can_delete": resource.can_delete(request, instance),
            "audit_history": audit_history,
        }
    )
    return Admin._render("admin/resource/show.html", ctx)


def create(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not resource.can_create(request):
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
    from hunt.admin.fields.boolean import Boolean as BooleanField
    from hunt.admin.fields.image import Image as ImageField
    from hunt.validation.validator import ValidationException

    allowed_attrs = {f.attribute for f in fields if not f._readonly}
    raw_data = request.all()
    data = {k: v for k, v in raw_data.items() if k in allowed_attrs}

    # Unchecked checkboxes are absent from POST data entirely. Explicitly set
    # them to False so instance.fill() sees the unchecked state.
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


def store(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not resource.can_create(request):
        raise HttpException(403, "Forbidden.")

    create_fields = [f for f in resource.fields() if f._show_on_create]
    rules = {f.attribute: "|".join(f._rules) for f in create_fields if f._rules}
    data = _collect_data(request, create_fields)

    if rules:
        Validator.make(data, rules).validate()

    stored = _store_image_fields(data, create_fields)
    instance = resource.model.create(stored)

    from hunt.admin.audit import AuditLog, _write_audit

    if isinstance(resource, AuditLog) and instance is not None:
        record_id = instance._attributes.get("id", "")
        _write_audit(_current_user_id(), type(resource).__name__, record_id, "create", {}, dict(instance._attributes))

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} created successfully.",
        f"{Admin.prefix}/resources/{resource_key}",
    )


def edit(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id)
    if not resource.can_update(request, instance):
        raise HttpException(403, "Forbidden.")

    edit_fields = [f for f in resource.fields() if f._show_on_edit]
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
        }
    )
    return Admin._render("admin/resource/edit.html", ctx)


def update(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id)
    if not resource.can_update(request, instance):
        raise HttpException(403, "Forbidden.")

    edit_fields = [f for f in resource.fields() if f._show_on_edit]
    rules = {f.attribute: "|".join(f._rules) for f in edit_fields if f._rules}
    data = _collect_data(request, edit_fields)

    if rules:
        Validator.make(data, rules).validate()

    stored = _store_image_fields(data, edit_fields)
    from hunt.http.request import UploadedFile

    final = {k: v for k, v in stored.items() if not isinstance(v, UploadedFile)}
    old_attrs = dict(instance._attributes)
    instance.fill(final)
    instance.save()

    from hunt.admin.audit import AuditLog, _write_audit

    if isinstance(resource, AuditLog):
        _write_audit(_current_user_id(), type(resource).__name__, id, "update", old_attrs, dict(instance._attributes))

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} updated successfully.",
        f"{Admin.prefix}/resources/{resource_key}/{id}",
    )


def destroy(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id)
    if not resource.can_delete(request, instance):
        raise HttpException(403, "Forbidden.")

    old_attrs = dict(instance._attributes)
    try:
        instance.delete()
    except Exception:
        return _flash_and_redirect(
            request,
            "admin_error",
            "Could not delete record. Please try again.",
            f"{Admin.prefix}/resources/{resource_key}",
        )

    from hunt.admin.audit import AuditLog, _write_audit

    if isinstance(resource, AuditLog):
        _write_audit(_current_user_id(), type(resource).__name__, id, "delete", old_attrs, {})

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} deleted successfully.",
        f"{Admin.prefix}/resources/{resource_key}",
    )

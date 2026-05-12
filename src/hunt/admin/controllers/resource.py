from __future__ import annotations

from typing import Any

from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.http.response import HttpException
from hunt.validation.validator import Validator


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

    query = resource.index_query(request)
    paginate_result = query.paginate(resource.per_page, page)

    index_fields = [f for f in resource.fields() if f.show_on_index]
    ctx = Admin._base_context(request)
    ctx.update({
        "title": resource.get_label_plural(),
        "resource": resource,
        "resource_key": resource_key,
        "items": paginate_result["data"],
        "pagination": paginate_result,
        "fields": index_fields,
        "search": request.query("search", ""),
        "sort": request.query("sort", ""),
        "dir": request.query("dir", "desc"),
        "filters": resource.filters(),
        "actions": resource.actions(),
        "metrics": [],
    })

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
    detail_fields = [f for f in resource.fields() if f.show_on_detail]
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

    ctx = Admin._base_context(request)
    ctx.update({
        "title": resource.title(instance),
        "resource": resource,
        "resource_key": resource_key,
        "instance": instance,
        "fields": detail_fields,
        "has_many_panels": has_many_panels,
        "related_data": related_data,
        "record_id": id,
    })
    return Admin._render("admin/resource/show.html", ctx)


def create(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not resource.can_create(request):
        raise HttpException(403, "Forbidden.")

    create_fields = [f for f in resource.fields() if f.show_on_create]
    ctx = Admin._base_context(request)
    ctx.update({
        "title": f"Create {resource.get_label()}",
        "resource": resource,
        "resource_key": resource_key,
        "fields": create_fields,
        "errors": {},
        "old": {},
    })
    return Admin._render("admin/resource/create.html", ctx)


def store(request: Request, resource_key: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    if not resource.can_create(request):
        raise HttpException(403, "Forbidden.")

    create_fields = [f for f in resource.fields() if f.show_on_create]
    rules = {f.attribute: "|".join(f._rules) for f in create_fields if f._rules}

    # Only accept values for attributes defined in the form's fields.
    allowed_attrs = {f.attribute for f in create_fields if not f._readonly}
    raw = request.all()
    data = {k: v for k, v in raw.items() if k in allowed_attrs}

    errors: dict = {}
    if rules:
        validator = Validator.make(data, rules)
        if validator.fails():
            errors = validator.errors()._errors

    if errors:
        ctx = Admin._base_context(request)
        ctx.update({
            "title": f"Create {resource.get_label()}",
            "resource": resource,
            "resource_key": resource_key,
            "fields": create_fields,
            "errors": errors,
            "old": data,
        })
        return Admin._render("admin/resource/create.html", ctx, status=422)

    try:
        resource.model.create(data)
    except Exception:
        ctx = Admin._base_context(request)
        ctx.update({
            "title": f"Create {resource.get_label()}",
            "resource": resource,
            "resource_key": resource_key,
            "fields": create_fields,
            "errors": {"_general": ["An error occurred while saving. Please try again."]},
            "old": data,
        })
        return Admin._render("admin/resource/create.html", ctx, status=422)

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

    edit_fields = [f for f in resource.fields() if f.show_on_edit]
    ctx = Admin._base_context(request)
    ctx.update({
        "title": f"Edit {resource.title(instance)}",
        "resource": resource,
        "resource_key": resource_key,
        "instance": instance,
        "fields": edit_fields,
        "errors": {},
        "old": instance._attributes,
        "record_id": id,
    })
    return Admin._render("admin/resource/edit.html", ctx)


def update(request: Request, resource_key: str, id: str) -> Response:
    from hunt.admin.application import Admin

    resource = _get_resource(resource_key)
    instance = _get_instance(resource, id)
    if not resource.can_update(request, instance):
        raise HttpException(403, "Forbidden.")

    edit_fields = [f for f in resource.fields() if f.show_on_edit]
    rules = {f.attribute: "|".join(f._rules) for f in edit_fields if f._rules}

    # Only accept values for attributes defined in the form's editable fields.
    allowed_attrs = {f.attribute for f in edit_fields if not f._readonly}
    raw = request.all()
    data = {k: v for k, v in raw.items() if k in allowed_attrs}

    errors: dict = {}
    if rules:
        validator = Validator.make(data, rules)
        if validator.fails():
            errors = validator.errors()._errors

    if errors:
        ctx = Admin._base_context(request)
        ctx.update({
            "title": f"Edit {resource.title(instance)}",
            "resource": resource,
            "resource_key": resource_key,
            "instance": instance,
            "fields": edit_fields,
            "errors": errors,
            "old": data,
            "record_id": id,
        })
        return Admin._render("admin/resource/edit.html", ctx, status=422)

    try:
        instance.fill(data)
        instance.save()
    except Exception:
        ctx = Admin._base_context(request)
        ctx.update({
            "title": f"Edit {resource.title(instance)}",
            "resource": resource,
            "resource_key": resource_key,
            "instance": instance,
            "fields": edit_fields,
            "errors": {"_general": ["An error occurred while saving. Please try again."]},
            "old": data,
            "record_id": id,
        })
        return Admin._render("admin/resource/edit.html", ctx, status=422)

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

    try:
        instance.delete()
    except Exception:
        return _flash_and_redirect(
            request,
            "admin_error",
            "Could not delete record. Please try again.",
            f"{Admin.prefix}/resources/{resource_key}",
        )

    return _flash_and_redirect(
        request,
        "admin_success",
        f"{resource.get_label()} deleted successfully.",
        f"{Admin.prefix}/resources/{resource_key}",
    )

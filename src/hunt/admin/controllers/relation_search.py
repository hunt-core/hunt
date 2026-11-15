from __future__ import annotations

import logging

from hunt.admin.authorization import allowed as _can
from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse

_log = logging.getLogger(__name__)


def search_relation(request: Request, resource_key: str) -> JsonResponse:
    from hunt.admin.application import Admin
    from hunt.admin.fields.belongs_to import BelongsTo
    from hunt.admin.fields.belongs_to_many import BelongsToMany

    resource_cls = Admin.find_resource(resource_key)
    if resource_cls is None:
        return JsonResponse({"results": []})
    resource = resource_cls()

    # The picker only exists on create/edit forms: require one of those
    # abilities on the owning resource.
    if not (_can(resource, "can_create", request) or _can(resource, "can_update", request)):
        raise HttpException(403, "Forbidden.")

    field_attr = request.query("field", "")
    q = str(request.query("q", "") or "").strip()[:200]

    target_field = None
    for field in resource.fields():
        if isinstance(field, BelongsTo) and field.attribute == field_attr and field._searchable:
            target_field = field
            break
        if isinstance(field, BelongsToMany) and field.attribute == field_attr:
            target_field = field
            break

    if target_field is None:
        return JsonResponse({"results": []})

    try:
        related_resource = target_field.related_resource_class()
    except Exception:
        _log.exception("Relation search: could not build the related resource for %r", field_attr)
        return JsonResponse({"results": []})

    # ...and the user must be allowed to list the related records.
    if not _can(related_resource, "can_view_any", request):
        raise HttpException(403, "Forbidden.")

    try:
        pk = getattr(related_resource.model, "primary_key", "id")
        if not isinstance(pk, str) or not pk:
            pk = "id"
        # index_query keeps any owner/tenant scope the related resource applies.
        query = related_resource.index_query(request)
        if q:
            query = related_resource._search_group(query, q)
        items = query.limit(20).get()
        results = [{"id": item._attributes.get(pk), "label": related_resource.title(item)} for item in items]
    except Exception:
        _log.exception("Relation search failed for %r", field_attr)
        results = []

    return JsonResponse({"results": results})

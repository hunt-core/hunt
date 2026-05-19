from __future__ import annotations

from hunt.http.request import Request
from hunt.http.response import JsonResponse


def search_relation(request: Request, resource_key: str) -> JsonResponse:
    from hunt.admin.application import Admin
    from hunt.admin.fields.belongs_to import BelongsTo

    resource_cls = Admin.find_resource(resource_key)
    if resource_cls is None:
        return JsonResponse({"results": []})
    resource = resource_cls()

    field_attr = request.query("field", "")
    q = str(request.query("q", "") or "").strip()[:200]

    target_field = None
    for field in resource.fields():
        if isinstance(field, BelongsTo) and field.attribute == field_attr and field._searchable:
            target_field = field
            break

    if target_field is None:
        return JsonResponse({"results": []})

    try:
        related_resource = target_field.related_resource_class()
        query = related_resource.model.query()
        if q and related_resource.search_columns:
            for i, col in enumerate(related_resource.search_columns):
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                if i == 0:
                    query = query.where(col, "LIKE", f"%{escaped}%")
                else:
                    try:
                        query = query.or_where(col, "LIKE", f"%{escaped}%")
                    except ValueError:
                        pass
        items = query.limit(20).get()
        results = [{"id": item._attributes.get("id"), "label": related_resource.title(item)} for item in items]
    except Exception:
        results = []

    return JsonResponse({"results": results})

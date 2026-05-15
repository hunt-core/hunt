from __future__ import annotations

from hunt.http.request import Request
from hunt.http.response import JsonResponse


def index(request: Request) -> JsonResponse:
    from hunt.admin.application import Admin

    q = str(request.query("q", "") or "").strip()
    if not q:
        return JsonResponse({"groups": []})
    q = q[:200]  # cap to avoid pathological LIKE patterns
    groups = []

    for resource_cls in Admin._resources:
        resource = resource_cls()
        if not resource.search_columns:
            continue

        try:
            query = resource.model.query()
            first_col = resource.search_columns[0]
            query = query.where(first_col, "LIKE", f"%{q}%")
            for col in resource.search_columns[1:]:
                try:
                    query = query.or_where(col, "LIKE", f"%{q}%")
                except ValueError:
                    pass
            items = query.limit(5).get()

            if not items:
                continue

            groups.append(
                {
                    "resource": resource_cls.get_label(),
                    "resource_key": resource_cls.slug(),
                    "resource_url": f"{Admin.prefix}/resources/{resource_cls.slug()}",
                    "items": [
                        {
                            "id": item._attributes.get("id"),
                            "title": resource.title(item),
                            "url": f"{Admin.prefix}/resources/{resource_cls.slug()}/{item._attributes.get('id')}",
                        }
                        for item in items
                    ],
                }
            )
        except Exception:
            continue

    return JsonResponse({"groups": groups})

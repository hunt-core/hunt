from __future__ import annotations

import logging

from hunt.admin.authorization import allowed as _can
from hunt.http.request import Request
from hunt.http.response import JsonResponse

_log = logging.getLogger(__name__)


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
        # Global search must not reveal records of resources the user cannot list.
        if not _can(resource, "can_view_any", request):
            continue

        try:
            pk = getattr(resource.model, "primary_key", "id")
            if not isinstance(pk, str) or not pk:
                pk = "id"
            # Start from index_query so an owner/tenant scope added there also
            # applies here, and AND the search on as one group so the OR
            # between columns cannot escape those constraints.
            query = resource._search_group(resource.index_query(request), q)
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
                            "id": item._attributes.get(pk),
                            "title": resource.title(item),
                            "url": f"{Admin.prefix}/resources/{resource_cls.slug()}/{item._attributes.get(pk)}",
                        }
                        for item in items
                    ],
                }
            )
        except Exception:
            _log.exception("Admin global search failed for %s", resource_cls.__name__)
            continue

    return JsonResponse({"groups": groups})

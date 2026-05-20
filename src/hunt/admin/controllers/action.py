from __future__ import annotations

import json

from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response


def run(request: Request, resource_key: str, action_slug: str) -> Response:
    from hunt.admin.application import Admin

    resource_cls = Admin.find_resource(resource_key)
    if resource_cls is None:
        raise HttpException(404, "Resource not found.")
    resource = resource_cls()

    # Require at least update permission to run any action.
    if not resource.can_update(request):
        raise HttpException(403, "Forbidden.")

    # Resolve selected IDs — accept comma-separated string or JSON array.
    raw_ids = request.input("ids", "")
    if isinstance(raw_ids, list):
        candidate_ids = [str(i) for i in raw_ids]
    elif raw_ids:
        raw_str = str(raw_ids).strip()
        if raw_str.startswith("["):
            try:
                candidate_ids = [str(i) for i in json.loads(raw_str)]
            except (json.JSONDecodeError, ValueError):
                candidate_ids = [s.strip() for s in raw_str.split(",") if s.strip()]
        else:
            candidate_ids = [s.strip() for s in raw_str.split(",") if s.strip()]
    else:
        candidate_ids = []

    # Find the action by slug.
    matched_action = None
    for action in resource.actions():
        if action.slug() == action_slug:
            matched_action = action
            break

    if matched_action is None:
        raise HttpException(404, "Action not found.")

    # Scope the IDs through index_query so that users cannot act on records
    # outside the scope they can see (prevents IDOR).
    try:
        accessible = {str(item._attributes.get("id")) for item in resource.index_query(request).get()}
    except Exception:
        accessible = set()

    safe_ids = [i for i in candidate_ids if i in accessible]

    # Load model instances for the verified IDs.
    instances = []
    for record_id in safe_ids:
        try:
            instance = resource.model.find(record_id)
            if instance is not None:
                instances.append(instance)
        except Exception:
            pass

    try:
        result = matched_action.handle(request, instances)
    except Exception:
        store = getattr(request, "_session", None)
        if store is not None:
            store.flash("admin_error", "The action could not be completed. Please try again.")
        return RedirectResponse(f"{Admin.prefix}/resources/{resource_key}")

    store = getattr(request, "_session", None)

    if result.type == "redirect":
        # Reject external redirects — only allow relative paths.
        url = result.url or ""
        if url.startswith(("http://", "https://", "//")):
            url = f"{Admin.prefix}/resources/{resource_key}"
        return RedirectResponse(url)

    if result.type == "download":
        resp = Response(
            content=result.download_content,
            status=200,
            headers={
                "Content-Type": result.download_content_type,
                "Content-Disposition": f'attachment; filename="{result.download_filename}"',
            },
        )
        return resp

    if store is not None:
        flash_key = "admin_success" if result.message_type == "success" else "admin_error"
        store.flash(flash_key, result.text)

    return RedirectResponse(f"{Admin.prefix}/resources/{resource_key}")

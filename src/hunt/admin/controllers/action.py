from __future__ import annotations

import json
import logging

from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response

_log = logging.getLogger(__name__)


# Upper bound on how many records one bulk action may touch.
MAX_ACTION_IDS = 1000


def _parse_ids(raw_ids: object) -> list[str]:
    """Accept a list, a JSON array or a comma-separated string of ids."""
    if isinstance(raw_ids, list):
        candidates = [str(i) for i in raw_ids]
    elif raw_ids:
        raw_str = str(raw_ids).strip()
        candidates = []
        if raw_str.startswith("["):
            try:
                parsed = json.loads(raw_str)
                if isinstance(parsed, list):
                    candidates = [str(i) for i in parsed]
            except (json.JSONDecodeError, ValueError):
                candidates = []
        if not candidates:
            candidates = [s.strip() for s in raw_str.strip("[]").split(",") if s.strip()]
    else:
        candidates = []
    unique: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def run(request: Request, resource_key: str, action_slug: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import AuditLog, _current_user_id, _write_audit, audit_safe
    from hunt.admin.authorization import _allow

    resource_cls = Admin.find_resource(resource_key)
    if resource_cls is None:
        raise HttpException(404, "Resource not found.")
    resource = resource_cls()
    index_url = f"{Admin.prefix}/resources/{resource_key}"
    store = getattr(request, "_session", None)

    # Find the action by slug.
    matched_action = None
    for action in resource.actions():
        if action.slug() == action_slug:
            matched_action = action
            break

    if matched_action is None:
        raise HttpException(404, "Action not found.")

    candidate_ids = _parse_ids(request.input("ids", ""))
    if not candidate_ids:
        if store is not None:
            store.flash("admin_error", "Please select at least one record.")
        return RedirectResponse(index_url)
    if len(candidate_ids) > MAX_ACTION_IDS:
        raise HttpException(422, f"Too many records selected (maximum {MAX_ACTION_IDS}).")

    # Load the selected records through index_query so users cannot act on
    # records outside the scope they can see (prevents IDOR). The query is
    # bounded by the selected ids; the table is never hydrated wholesale.
    pk = getattr(resource.model, "primary_key", "id")
    if not isinstance(pk, str) or not pk:
        pk = "id"
    try:
        query = resource.index_query(request)
        if getattr(matched_action, "with_trashed", False) is True:
            query = query.with_trashed()
        loaded = query.where_in(pk, candidate_ids).get()
    except Exception:
        _log.exception("Admin action %s: could not load the selected records", action_slug)
        loaded = []

    # Each record must pass the action's own authorization (delete permission
    # for a bulk delete, per-record policies included).
    instances = []
    for instance in loaded:
        try:
            permitted = _allow(matched_action.authorize(request, resource, instance))
        except Exception:
            _log.exception("Admin action %s: authorize() raised; record skipped", action_slug)
            permitted = False
        if permitted:
            instances.append(instance)

    if not instances:
        raise HttpException(403, "Forbidden.")

    audited = isinstance(resource, AuditLog)
    snapshots = [(i._attributes.get(pk), audit_safe(resource, i._attributes)) for i in instances] if audited else []

    matched_action.resource = resource
    try:
        result = matched_action.handle(request, instances)
    except Exception:
        _log.exception("Admin action %s failed", action_slug)
        if store is not None:
            store.flash("admin_error", "The action could not be completed. Please try again.")
        return RedirectResponse(index_url)

    if audited:
        user_id = _current_user_id()
        for record_id, old_attrs in snapshots:
            _write_audit(user_id, type(resource).__name__, record_id, f"action:{action_slug}", old_attrs, {})

    if result.type == "redirect":
        # Reject external redirects — only allow relative paths.
        url = result.url or ""
        if url.startswith(("http://", "https://", "//")):
            url = index_url
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

    return RedirectResponse(index_url)

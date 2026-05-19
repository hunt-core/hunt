from __future__ import annotations

import json
import time
from datetime import datetime

from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response


def _raw():
    from hunt.database.connection import raw

    return raw


def _flash_and_redirect(request: Request, key: str, message: str, url: str) -> RedirectResponse:
    store = getattr(request, "_session", None)
    if store is not None:
        store.flash(key, message)
    return RedirectResponse(url)


def _extract_job_class(payload_json: str) -> str:
    try:
        outer = json.loads(payload_json)
        inner = json.loads(outer.get("body", "{}"))
        full = inner.get("class", "")
        return full.split(".")[-1] if full else "Unknown"
    except Exception:
        return "Unknown"


def _fmt_time(ts: int | None) -> str:
    if ts is None:
        return "—"
    try:
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _job_status(row: dict) -> str:
    now = int(time.time())
    if row.get("reserved_at") is not None:
        return "Processing"
    if row.get("available_at") is not None and int(row["available_at"]) > now:
        return "Delayed"
    return "Pending"


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    raw = _raw()
    ctx = Admin._base_context(request)

    pending_jobs: list[dict] = []
    failed_jobs: list[dict] = []
    jobs_table_missing = False
    failed_table_missing = False

    try:
        rows = raw("SELECT * FROM jobs ORDER BY id DESC").fetchall()
        for row in rows:
            d = dict(row._mapping)
            d["_status"] = _job_status(d)
            d["_job_class"] = _extract_job_class(d.get("payload", ""))
            d["_created_at_fmt"] = _fmt_time(d.get("created_at"))
            pending_jobs.append(d)
    except Exception:
        jobs_table_missing = True

    try:
        rows = raw("SELECT * FROM jobs_failed ORDER BY id DESC").fetchall()
        for row in rows:
            d = dict(row._mapping)
            d["_job_class"] = _extract_job_class(d.get("payload", ""))
            d["_failed_at_fmt"] = _fmt_time(d.get("failed_at"))
            exc = d.get("exception") or ""
            d["_exception_short"] = exc[:300] + ("…" if len(exc) > 300 else "")
            failed_jobs.append(d)
    except Exception:
        failed_table_missing = True

    ctx.update(
        {
            "title": "Queue",
            "pending_jobs": pending_jobs,
            "failed_jobs": failed_jobs,
            "jobs_table_missing": jobs_table_missing,
            "failed_table_missing": failed_table_missing,
        }
    )
    return Admin._render("admin/queue/index.html", ctx)


def retry(request: Request, id: str) -> Response:
    from hunt.admin.application import Admin

    raw = _raw()
    try:
        row = raw("SELECT * FROM jobs_failed WHERE id = :id", {"id": id}).fetchone()
        if row is None:
            raise HttpException(404, "Failed job not found.")
        job = dict(row._mapping)
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, NULL, :now)",
            {"queue": job["queue"], "payload": job["payload"], "now": int(time.time())},
        )
        raw("DELETE FROM jobs_failed WHERE id = :id", {"id": id})
    except HttpException:
        raise
    except Exception as exc:
        return _flash_and_redirect(request, "admin_error", str(exc), f"{Admin.prefix}/queue")

    return _flash_and_redirect(request, "admin_success", "Job re-queued successfully.", f"{Admin.prefix}/queue")


def delete_failed(request: Request, id: str) -> Response:
    from hunt.admin.application import Admin

    raw = _raw()
    try:
        raw("DELETE FROM jobs_failed WHERE id = :id", {"id": id})
    except Exception as exc:
        return _flash_and_redirect(request, "admin_error", str(exc), f"{Admin.prefix}/queue")

    return _flash_and_redirect(request, "admin_success", "Failed job deleted.", f"{Admin.prefix}/queue")


def flush(request: Request) -> Response:
    from hunt.admin.application import Admin

    raw = _raw()
    try:
        raw("DELETE FROM jobs_failed")
    except Exception as exc:
        return _flash_and_redirect(request, "admin_error", str(exc), f"{Admin.prefix}/queue")

    return _flash_and_redirect(request, "admin_success", "All failed jobs flushed.", f"{Admin.prefix}/queue")

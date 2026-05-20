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


def _format_payload(payload_json: str) -> str:
    try:
        outer = json.loads(payload_json)
        body_str = outer.get("body", "{}")
        body = json.loads(body_str) if isinstance(body_str, str) else body_str
        return json.dumps(body, indent=2)
    except Exception:
        return payload_json


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    raw = _raw()
    ctx = Admin._base_context(request)
    now = int(time.time())

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
            d["_payload_formatted"] = _format_payload(d.get("payload", "{}"))
            failed_jobs.append(d)
    except Exception:
        failed_table_missing = True

    # ── Throughput (last 24 h from jobs_history) ──────────────────────
    throughput: list[dict] = []
    queue_stats: list[dict] = []
    history_table_missing = False

    try:
        cutoff_24h = now - 86400
        cutoff_1h = now - 3600

        history_rows = raw(
            "SELECT job_class, queue, duration_ms, finished_at, status"
            " FROM jobs_history WHERE finished_at >= :cutoff ORDER BY finished_at DESC",
            {"cutoff": cutoff_24h},
        ).fetchall()
        history = [dict(r._mapping) for r in history_rows]

        # 24-hour throughput bucketed by hour
        now_hour = now // 3600
        buckets: dict[int, dict] = {}
        for i in range(24):
            h = now_hour - (23 - i)
            buckets[h] = {"hour_label": f"{h % 24:02d}:00", "completed": 0, "failed": 0, "total": 0}
        for row in history:
            h = row["finished_at"] // 3600
            if h in buckets:
                buckets[h][row["status"]] = buckets[h].get(row["status"], 0) + 1
                buckets[h]["total"] += 1
        max_total = max((b["total"] for b in buckets.values()), default=1) or 1
        for b in buckets.values():
            b["bar_pct"] = int(b["total"] / max_total * 100)
            b["completed_pct"] = int(b["completed"] / b["total"] * 100) if b["total"] else 0
        throughput = list(buckets.values())

        # Per-queue breakdown (pending now + last-hour completions)
        queue_pending: dict[str, int] = {}
        try:
            rows = raw("SELECT queue, COUNT(*) as cnt FROM jobs WHERE reserved_at IS NULL GROUP BY queue").fetchall()
            for r in rows:
                queue_pending[r.queue] = int(r.cnt)
        except Exception:
            pass

        queue_completed: dict[str, int] = {}
        queue_failed_1h: dict[str, int] = {}
        for row in history:
            if row["finished_at"] >= cutoff_1h:
                q = row["queue"]
                if row["status"] == "completed":
                    queue_completed[q] = queue_completed.get(q, 0) + 1
                else:
                    queue_failed_1h[q] = queue_failed_1h.get(q, 0) + 1

        all_queues = sorted(set(list(queue_pending) + list(queue_completed) + list(queue_failed_1h)))
        for q in all_queues:
            queue_stats.append(
                {
                    "queue": q,
                    "pending": queue_pending.get(q, 0),
                    "processed_1h": queue_completed.get(q, 0),
                    "failed_1h": queue_failed_1h.get(q, 0),
                }
            )

    except Exception:
        history_table_missing = True

    ctx.update(
        {
            "title": "Queue",
            "pending_jobs": pending_jobs,
            "failed_jobs": failed_jobs,
            "jobs_table_missing": jobs_table_missing,
            "failed_table_missing": failed_table_missing,
            "throughput": throughput,
            "queue_stats": queue_stats,
            "history_table_missing": history_table_missing,
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

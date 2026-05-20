# hunt backlog plan — M12–M19

Templates use **Tailwind CSS** (not Bootstrap).

---

## M12 — Soft Delete *(small — mostly wired, filling gaps)*

`_soft_deletes`, `delete()`, `restore()`, and `force_delete()` are already on `Model`.
`Blueprint.soft_deletes()` already exists. What's missing:

| # | Task | File |
|---|------|------|
| 12.1 | `SoftDeletes` mixin class users add (`class Post(SoftDeletes, Model)`) that sets `_soft_deletes = True` | `src/hunt/database/soft_deletes.py` (new) |
| 12.2 | `QueryBuilder.only_trashed()` — filter to only `deleted_at IS NOT NULL` | `query_builder.py` |
| 12.3 | Docs update | `hunt-docs/models`, `hunt-docs/migrations` |

---

## M13 — Pagination Helpers *(small — extends existing)*

| # | Task | File |
|---|------|------|
| 13.1 | `PaginationResult` class: wraps the dict, adds `.links(base_url)` → list of `{"url", "label", "active"}` dicts, `.has_more_pages`, `.prev_page_url`, `.next_page_url`. Serialises to dict for JSON responses. | `src/hunt/pagination.py` (new) |
| 13.2 | `QueryBuilder.simple_paginate(per_page, page)` — no `COUNT(*)`, returns `PaginationResult` with `{"data", "per_page", "current_page", "has_more"}` | `query_builder.py` |
| 13.3 | `paginate()` return value becomes `PaginationResult` (backwards-compatible: still iterable, still JSON-serialisable, `dict()` still works) | `query_builder.py` |
| 13.4 | Jinja2 template macro `{% pagination result, route_name %}` renders **Tailwind**-styled prev/next/page links | `src/hunt/views/pagination.html` (new) |
| 13.5 | Docs | `hunt-docs/query-builder` |

---

## M14 — API Resources *(medium — new, self-contained)*

| # | Task | File |
|---|------|------|
| 14.1 | `ApiResource` base class: `__init__(self, instance)`, abstract `to_array(request)`, `to_response(request)` → `JsonResponse`, `when(cond, value, default=None)` conditional helper, `merge_when(cond, dict)` | `src/hunt/http/resources.py` (new) |
| 14.2 | `ApiResourceCollection(ApiResource)`: `__init__(self, items, resource_class)`, `to_array()` maps each item, wraps in `{"data": [...]}` | same file |
| 14.3 | `ApiResource.collection(items)` classmethod shorthand | same file |
| 14.4 | Kernel auto-detects `ApiResource` return values from controllers and calls `.to_response(request)` | `kernel.py` |
| 14.5 | Docs | `hunt-docs/responses` |

---

## M15 — Redis Driver *(medium — skeleton exists, gaps to fill)*

| # | Task | File |
|---|------|------|
| 15.1 | Fix `pop()` — `attempts` is hardcoded `1`; use a Redis hash to track attempt counts per job ID | `src/hunt/queue/drivers/redis.py` |
| 15.2 | Fix `fail()` — writes to Redis but worker reads failed jobs from DB `jobs_failed`; write to DB table instead | `redis.py` |
| 15.3 | `RedisSessionStore`: GET/SET/DEL on `hunt_session:{id}` keys with TTL | `src/hunt/session/redis_store.py` (new) |
| 15.4 | Shared `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` env vars, single `redis_client()` factory | `src/hunt/redis_connection.py` (new) |
| 15.5 | `SESSION_DRIVER=redis` in `config/session.py` picks up `RedisSessionStore` | session middleware / provider |
| 15.6 | Docs | `hunt-docs/sessions`, `hunt-docs/queues` |

---

## M16 — Debug Panel *(medium — builds on M11 query logging)*

HTML toolbar injected at the bottom of every HTML response when `APP_DEBUG=true`:

| # | Task | File |
|---|------|------|
| 16.1 | `DebugPanel` middleware: after response is built, if content-type is `text/html`, injects toolbar HTML before `</body>` | `src/hunt/http/middleware/debug_panel.py` (new) |
| 16.2 | Per-request data: request (method, path, headers), matched route (name, params), query list from `debug.py` (SQL + ms), total elapsed ms | `debug.py` extended with `get_query_log()` |
| 16.3 | Panel is a self-contained `<div>` with **Tailwind CDN** + vanilla JS for tab switching. Tabs: **Request**, **Route**, **Queries** (total count + time), **Session** | inline HTML constant |
| 16.4 | Docs — note it auto-enables when `APP_DEBUG=true`; add to global middleware list | new `hunt-docs/debugging` page |

---

## M17 — Queue Dashboard *(medium — extends existing admin panel)*

| # | Task | File |
|---|------|------|
| 17.1 | `jobs_history` table migration (via `hunt queue:table`): `job_class`, `queue`, `duration_ms`, `finished_at`, `status` | migration generator |
| 17.2 | Worker writes to `jobs_history` on success and failure | `queue_work.py` |
| 17.3 | Dashboard gains **Throughput** section: jobs processed per hour for last 24 h (GROUP BY on `jobs_history`) | admin queue template + controller |
| 17.4 | Per-queue breakdown table: queue name, pending count, processed (last hour), failed (last hour) | same |
| 17.5 | Failed job detail modal: full exception + full payload JSON expandable | template update |
| 17.6 | Docs | `hunt-docs/queues` |

---

## M18 — Subdomain Routing *(medium-large — touches router core)*

```python
# Static subdomain
Router.domain("api").group(lambda r: r.get("/users", handler))

# Parameterised subdomain — {account} captured as route param
Router.domain("{account}").group(lambda r: r.get("/dashboard", handler))
```

| # | Task | File |
|---|------|------|
| 18.1 | `Request.host` property (strips port); `Request.subdomain(root_domain)` strips root and returns prefix | `request.py` |
| 18.2 | `Route._domain: str \| None` — optional domain pattern (static or `{param}`) | `route.py` |
| 18.3 | `Route.matches(method, path, host)` — extend matching to validate domain and extract subdomain params | `route.py` |
| 18.4 | `Router.dispatch(method, path, host=None)` — pass host through; routes without `_domain` match any host | `router.py` |
| 18.5 | Kernel passes `request.host` to `router.dispatch()` | `kernel.py` |
| 18.6 | `Router.domain(pattern)` context manager — sets group-level domain constraint alongside group prefix | `router.py` |
| 18.7 | Subdomain params merged into `request.route_params` | |
| 18.8 | Docs | `hunt-docs/routing` |

---

## M19 — Two-Factor Authentication *(large — new auth flow)*

TOTP-based (authenticator app). Password confirmation required to enable.

| # | Task | File |
|---|------|------|
| 19.1 | Migration: `two_factor_secret` (text nullable), `two_factor_enabled` (bool default false), `two_factor_recovery_codes` (text nullable) on users table | migration generator |
| 19.2 | `TwoFactor` service: `generate_secret()`, `qr_code_url(secret, email, app_name)`, `verify(secret, code)`, `generate_recovery_codes(n=8)` — wraps `pyotp` | `src/hunt/auth/two_factor.py` (new) |
| 19.3 | Enable flow: `GET /two-factor/setup` (confirm password form) → `POST /two-factor/setup` (verify password, generate secret, show QR + recovery codes) → `POST /two-factor/confirm` (verify TOTP code, activate) | `src/hunt/auth/controllers/two_factor.py` (new) |
| 19.4 | Disable flow: `DELETE /two-factor` (password required) | same |
| 19.5 | Login challenge: after `Auth.attempt()` succeeds, if `two_factor_enabled`, stash `_2fa_pending=user_id` in session and redirect to `GET /two-factor/challenge` → `POST /two-factor/challenge` (accepts TOTP code or recovery code, completes login) | `manager.py` + challenge controller |
| 19.6 | `TwoFactorMiddleware`: if `_2fa_pending` in session, redirect to challenge regardless of route | `src/hunt/auth/middleware/two_factor.py` (new) |
| 19.7 | `hunt make:2fa-controllers` scaffolds routes + **Tailwind** templates | console |
| 19.8 | Docs | `hunt-docs/authentication` |

---

## Dependency notes

```
M12 (soft delete)   — no deps
M13 (pagination)    — no deps
M14 (api resources) — no deps
M15 (redis)         — no deps
M16 (debug panel)   — M11 query log (done)
M17 (queue dash)    — M11 job history (queue_work.py already modified)
M18 (subdomain)     — no deps
M19 (2fa)           — M15 recommended (Redis sessions for 2FA state)
```

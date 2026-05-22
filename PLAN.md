# Production Readiness Plan

## M26 — Async-safe ORM

**Problem:** Every `Model.all()`, `.find()`, `.save()` calls synchronous SQLAlchemy Core on the event loop thread, blocking all concurrent requests.

**Approach:** Wrap sync DB calls in `asyncio.get_event_loop().run_in_executor(None, fn)` inside the framework's query-dispatch layer. This keeps the public API identical (`User.all()` still works), adds no new dependencies, and unblocks the event loop without requiring a full rewrite to `asyncpg`/`aiomysql`.

**Scope:** One method (`_run_sync`) in `src/hunt/database/orm/model.py` and a thin async wrapper on every public query method. No schema changes.

---

## M27 — Remove stubs from critical paths

**Problem:** The two stubs silently succeed where they should fail or error.

**`ApiAuth`:** `_resolve_user()` always returns `None`, so every protected route is always unauthorized. Fix: raise `NotImplementedError` with a message pointing the developer to the implementation pattern. Fail loudly in dev; don't silently block all traffic.

**`BillingController.webhook()`:** Skips Stripe signature verification. Fix: check for `STRIPE_WEBHOOK_SECRET` env var and verify with `stripe.Webhook.construct_event()`; if secret is missing, return 500 with a clear config error rather than silently processing.

**Scope:** Edit `starters/saas.py` and `starters/api.py` to emit the corrected stub code. No new files.

---

## M28 — Production server guidance

**Problem:** `hunt serve` hard-codes `reload=True` default and gives no path to production deployment.

**Approach:** Two parts:
1. Add a `hunt serve:production` command that runs Uvicorn with `workers=cpu_count()*2+1`, `reload=False`, `access_log=True`, and prints a checklist of env vars to set (`APP_ENV=production`, `DATABASE_URL`, etc.)
2. Add a `/docs/deployment/` page to hunt-docs covering Gunicorn + Uvicorn workers, systemd unit file, and reverse-proxy (nginx) config.

**Scope:** New `src/hunt/console/commands/serve_production.py`, register it in the CLI group, new hunt-docs page.

---

## M29 — Observability baseline

**Problem:** No health check, no structured logging, no request-id, no error hook.

**Approach:**
1. **Health endpoint** — register `GET /health` in the core router (not starter-dependent) returning `{"status":"ok","version":"<APP_VERSION>"}`. Opt-out via `HEALTH_CHECK_ENABLED=false`.
2. **Request-ID middleware** — ship a `RequestId` middleware that reads `X-Request-ID` header or generates a UUID, stores it on the request object, and echoes it in the response header. Auto-registered when `APP_ENV != "testing"`.
3. **Structured logging** — add a `LOG_FORMAT=json|text` env var; when `json`, emit log records as `{"level":…,"message":…,"request_id":…,"ts":…}` using Python's `logging` with a custom formatter. Default `text` for dev, document `json` for prod.
4. **Error hook** — add `App.on_error(callable)` in `Application`; the default handler logs the traceback. If `SENTRY_DSN` is set in env, auto-initialize the Sentry SDK and attach it as the error handler.

**Scope:** New middleware file, updates to `Application`, new env-var docs section.

---

## Order

1. **M27** — no new dependencies, pure correctness fix
2. **M28** — high user-visible value
3. **M29** — foundational, touches the most code
4. **M26** — highest risk, needs integration tests with a real DB

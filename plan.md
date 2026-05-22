# hunt backlog plan — M12–M25

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
M20 (scaffolding)   — no deps (extends existing make commands)
M21 (components)    — no deps (extends view engine)
M22 (form requests) — no deps (extends validation + http)
M23 (introspection) — no deps (read-only CLI commands)
M24 (starter kits)  — M20 recommended (reuses scaffold commands)
M25 (live reload)   — no deps (extends hunt serve)
```

---

## AI-Agent Website Builder Track (M20–M25)

These milestones make hunt the fastest framework for AI agents to build
production websites. The core insight: agents are blocked by **boilerplate
volume**, **convention discovery**, and **slow feedback loops**. Every
milestone below eliminates one of those blockers.

---

## M20 — Supercharged Scaffolding *(medium — extends make commands)*

The single highest-leverage change. One command should go from nothing to a
fully wired CRUD feature.

```bash
# generates Model + migration + controller (full CRUD) + views + routes
hunt make:crud Post --fields="title:string body:text published:bool"

# generates Model + migration only
hunt make:model Post --migration

# generates model + migration + controller + resource + routes (API)
hunt make:api Post --fields="title:string body:text"
```

| # | Task | File |
|---|------|------|
| 20.1 | `--migration` / `-m` flag on `hunt make:model` — generates a migration alongside the model, inferring the table name | `make/model.py` |
| 20.2 | `--controller` / `-c` flag on `hunt make:model` — generates a resourceful controller stub alongside the model | `make/model.py` |
| 20.3 | `hunt make:crud <Name> --fields="col:type ..."` — single command that creates model + migration (with columns) + resourceful controller + index/show/create/edit Blade views + appends CRUD routes to `routes/web.py` | `make/crud.py` (new) |
| 20.4 | `hunt make:api <Name> --fields="col:type ..."` — model + migration + API controller (index/show/store/update/destroy returning JSON) + `ApiResource` class + appends routes to `routes/api.py` | `make/api_scaffold.py` (new) |
| 20.5 | `hunt make:form <Name>Request` — generates a `FormRequest` class stub with a `rules()` method (see M22) | `make/form.py` (new) |
| 20.6 | Field type map: `string→VARCHAR(255)`, `text→TEXT`, `int→INTEGER`, `bool→BOOLEAN`, `timestamp→TIMESTAMP`, `uuid→UUID`, `json→JSON` used in both migration and view generation | shared `field_types.py` utility |
| 20.7 | Generated views use real column names as field labels; forms are pre-wired with `@csrf` and `@errors` | view generator |
| 20.8 | Docs | `hunt-docs/scaffolding` (new page) |

---

## M21 — UI Component Library *(medium — extends view engine)*

Agents write HTML badly. Components give them building blocks they can name
rather than construct.

```html
@component('card', ['title' => 'Hello', 'body' => $post->excerpt])
@component('table', ['headers' => ['Title','Date'], 'rows' => $posts])
@component('alert', ['type' => 'success', 'message' => 'Saved!'])
@component('button', ['label' => 'Submit', 'type' => 'submit'])
@component('modal', ['id' => 'confirm', 'title' => 'Delete post?'])
@component('badge', ['color' => 'red', 'text' => 'Draft'])
@component('navbar', ['brand' => config('app.name'), 'links' => $navLinks])
```

| # | Task | File |
|---|------|------|
| 21.1 | `@component(name, props)` directive — resolves `resources/views/components/<name>.html` first, then falls back to the package's built-in components | `view/directives.py` |
| 21.2 | `@slot` / `@endslot` directive — allows callers to inject named HTML blocks into components | `view/directives.py` |
| 21.3 | Built-in Tailwind components: `card`, `alert` (success/error/warning/info), `button` (primary/secondary/danger), `badge`, `table` (headers + rows), `modal` (dialog element), `navbar`, `sidebar`, `empty-state`, `form-group` (label + input + error), `pagination` (wraps existing pagination macro) | `src/hunt/views/components/*.html` (new directory) |
| 21.4 | `hunt make:component <Name>` — creates `resources/views/components/<name>.html` stub | `make/component.py` (new) |
| 21.5 | `hunt vendor:publish --tag=components` — copies built-in components into the app for customisation | `vendor_publish.py` extended |
| 21.6 | Docs with rendered preview of each component | `hunt-docs/components` (new page) |

---

## M22 — Form Requests *(small-medium — extends validation + http)*

Moves validation out of controllers so agents don't have to write the same
try/except boilerplate every time.

```python
class CreatePostRequest(FormRequest):
    def rules(self):
        return {
            "title": "required|min:3|max:255",
            "body":  "required",
            "published": "bool",
        }

class PostController(Controller):
    def store(self, request: CreatePostRequest) -> Response:
        # request is already validated — no try/except needed
        post = Post.create(request.validated())
        return self.redirect("/posts")
```

| # | Task | File |
|---|------|------|
| 22.1 | `FormRequest(Request)` base class: `rules() → dict`, `messages() → dict` (optional), `authorize() → bool` (default `True`), `validated() → dict` (only keys in `rules()`), `after_validation(validator)` hook | `src/hunt/http/form_request.py` (new) |
| 22.2 | Kernel type-hints introspection: when a controller method's parameter is annotated with a `FormRequest` subclass, the kernel instantiates it, runs validation, and if invalid redirects back with `errors` and `old` input in the session (same as Laravel's behaviour) | `http/kernel.py` |
| 22.3 | `ValidationException` raised by `FormRequest.validate()` is caught by the kernel exception handler; produces a redirect-back for HTML requests and a `422` JSON body for API requests | `exceptions/handler.py` |
| 22.4 | `hunt make:form <Name>Request` scaffold (see M20.5) | — |
| 22.5 | `@old('field')` Blade directive — restores previous input value after a failed form submission | `view/directives.py` |
| 22.6 | Docs | `hunt-docs/validation` extended |

---

## M23 — App Introspection CLI *(small — read-only commands)*

Agents need to read the current app state without grepping source files.
These commands output JSON so an agent can parse them directly.

```bash
hunt app:info              # JSON dump of routes, models, migrations, env keys
hunt route:list --json     # extend existing command with --json flag
hunt db:status             # which migrations have run vs. pending
hunt config:show           # resolved config values (secrets redacted)
```

| # | Task | File |
|---|------|------|
| 23.1 | `hunt app:info [--json]` — outputs app name, env, framework version, installed providers, registered routes (count), model files (count), migration status summary | `commands/app_info.py` (new) |
| 23.2 | `--json` flag on `hunt route:list` — prints the existing route table as a JSON array `[{"method","uri","name","action"}]` | `commands/route_list.py` |
| 23.3 | `hunt db:status [--json]` — lists each migration file with `ran`/`pending` status, similar to `artisan migrate:status` | `commands/db/status.py` (new) |
| 23.4 | `hunt config:show [key] [--json]` — dumps resolved config (env vars expanded); masks values matching `*secret*`, `*key*`, `*password*`, `*token*` | `commands/config_show.py` (new) |
| 23.5 | Docs | `hunt-docs/cli` extended |

---

## M24 — Starter Kits *(medium — extends `hunt new`)*

Zero-to-running-app in one command. Eliminates the setup phase entirely for
common website patterns.

```bash
hunt new myblog  --starter=blog   # blog: posts, categories, tags, auth, admin
hunt new myapi   --starter=api    # REST API: versioned routes, JWT auth, resources
hunt new mysaas  --starter=saas   # SaaS: auth, tenancy, billing hooks, admin
```

| # | Task | File |
|---|------|------|
| 24.1 | `hunt new <name> --starter=<kit>` flag — after creating the skeleton, overlays starter-kit files on top | `commands/new.py` extended |
| 24.2 | **`blog` kit** — `Post` / `Category` / `Tag` models + migrations + factories; `PostController` with full CRUD + views (index, show, create, edit); Auth routes pre-wired; admin resources for Post and Category | `starters/blog/` directory in package |
| 24.3 | **`api` kit** — versioned route groups (`/api/v1`); `UserResource` + `ApiResource` example; JWT-style token auth stub (`hunt make:token-auth`); `api` rate-limiting middleware; OpenAPI comment hints in route file | `starters/api/` |
| 24.4 | **`saas` kit** — everything from `blog` auth; `Team` / `Membership` models; subdomain routing pre-wired for tenant isolation; `BillingController` stub; `plan` column on teams | `starters/saas/` |
| 24.5 | Each kit ships a `README.md` inside the generated app explaining what was created and what to run next | kit template |
| 24.6 | Docs | `hunt-docs/installation` extended with starter kit section |

---

## M25 — Live Reload Dev Server *(small — extends `hunt serve`)*

Tight feedback loops are critical for agent iteration. Currently `hunt serve`
is a static Uvicorn wrapper — agents must manually restart after every change.

```bash
hunt serve --reload   # watches app/, config/, resources/, routes/ and restarts
hunt serve --reload --open   # also opens the browser
```

| # | Task | File |
|---|------|------|
| 25.1 | `--reload` flag on `hunt serve` — passes `--reload` and `--reload-dir` options to Uvicorn covering `app/`, `config/`, `resources/`, `routes/` | `commands/serve.py` |
| 25.2 | `--open` flag — after server starts, opens `http://localhost:{port}` in the default browser (`webbrowser.open`) | `commands/serve.py` |
| 25.3 | `--port` flag (default `8000`) already exists; ensure `--reload` and `--port` compose correctly | `commands/serve.py` |
| 25.4 | When `APP_DEBUG=true` and `--reload` is active, print a clear "Watching for changes…" line so agents know reload is active | `commands/serve.py` |
| 25.5 | Docs | `hunt-docs/cli` — serve section updated |

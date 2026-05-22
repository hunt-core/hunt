# Changelog

All notable changes to hunt are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
hunt uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.2.41] — 2026-05-22

### Fixed

- **2FA views missing from package** — Added all five two-factor authentication views (`setup`, `confirm`, `recovery`, `challenge`, `manage`) to `src/hunt/views/auth/two_factor/`. The `ViewFactory` falls back to built-in package views when no app override exists, so 2FA routes now render correctly after running `hunt make:2fa-controllers` without any additional steps.
- **`make:2fa-controllers` wrote views to wrong path** — `_write_templates` was writing to `templates/auth/two_factor/` instead of `resources/views/auth/two_factor/`. Views written by the command now land in the correct app views directory and can override the built-in package views.

---

## [0.2.40] — 2026-05-22

### Added

**Async-safe ORM (M26)**
- **`_run_sync(fn, *args, **kwargs)`** — async helper in `hunt.database.query_builder` that runs any sync callable in the default thread-pool executor via `asyncio.get_running_loop().run_in_executor(None, ...)`. This is the single chokepoint that unblocks the ASGI event loop for all ORM calls.
- **`QueryBuilder` async execution methods** — every terminal method now has an `async_*` twin that delegates to `_run_sync`: `async_get`, `async_first`, `async_first_or_fail`, `async_find`, `async_count`, `async_exists`, `async_min`, `async_max`, `async_avg`, `async_sum`, `async_pluck`, `async_paginate`, `async_insert`, `async_insert_many`, `async_update`, `async_delete`.
- **`Model` async methods** — instance: `async_save`, `async_delete`, `async_restore`; class: `async_find`, `async_find_or_fail`, `async_create`, `async_first_or_create`, `async_update_or_create`.
- Existing sync API (`User.all().get()`, `model.save()`, etc.) is fully preserved — CLI commands, migrations, seeders, and sync tests require no changes.
- 8 new async unit tests covering create, find, save/update, delete, query_get, count/exists, first, update_query, and pluck via the async path.

### Usage

```python
# In an async controller action — DB runs in a thread pool, event loop stays free
class UserController(Controller):
    async def index(self, request: Request) -> Response:
        users = await User.query().async_get()
        return self.json([u.to_dict() for u in users])

    async def store(self, request: Request) -> Response:
        user = await User.async_create(request.only("name", "email"))
        return self.json(user.to_dict(), status=201)

    async def destroy(self, request: Request, id: int) -> Response:
        user = await User.async_find_or_fail(id)
        await user.async_delete()
        return self.json({}, status=204)
```

---

## [0.2.39] — 2026-05-22

### Added

**Observability baseline (M29)**

- **`GET /health`** — built-in health-check endpoint in `HttpKernel`. Returns `{"status":"ok","version":"<version>"}` with no auth, no middleware, and no body parsing overhead. Opt-out via `HEALTH_CHECK_ENABLED=false`.

- **`RequestId` middleware** (`hunt.http.middleware.request_id`) — reads `X-Request-ID` from the incoming request (e.g. stamped by a gateway) or generates a UUID4. Stores the ID on `request.request_id` and in a `ContextVar` accessible via `current_request_id()`. Echoes the ID in the `X-Request-ID` response header. Auto-registered as the first global middleware when `APP_ENV != "testing"`.

- **`hunt.ctx.request_id`** — shared `ContextVar[str]` for the current request ID, importable by any layer that needs it (logging, error reporters, background tasks).

- **Structured JSON logging** — set `LOG_FORMAT=json` to switch all log channels (`file`, `daily`, `stderr`) to single-line JSON output: `{"ts":"…","level":"…","message":"…","request_id":"…"}`. `request_id` is populated from the context var automatically. Default remains `LOG_FORMAT=text`.

- **`Application.on_error(handler)`** — register a callable `(exc, request)` that is invoked after every unhandled exception reaches the kernel. Errors in the hook are silently swallowed so the original 500 response is always sent.

- **Sentry auto-init** — if `SENTRY_DSN` is set in the environment, `Application.__init__` initialises the Sentry SDK and registers `sentry_sdk.capture_exception` as an error hook automatically. A `RuntimeWarning` is emitted if `SENTRY_DSN` is set but `sentry-sdk` is not installed.

---

## [0.2.38] — 2026-05-22

### Added

**Production server command (M28)**
- **`hunt serve:production`** — new CLI command that starts a production-grade Uvicorn server: `reload=False`, `access_log=True`, workers default to `(2 × CPU cores) + 1`, host defaults to `0.0.0.0`. Accepts `--host`, `--port`, and `--workers N` options.
- On startup, the command inspects the environment and prints stderr warnings for common misconfigurations: `APP_ENV` not set to `production`, `APP_DEBUG=true`, missing `APP_KEY`, and no database configured.
- Deployment docs updated: added section 7 "Start the server" documenting `hunt serve:production` with usage examples and a callout about running behind a reverse proxy; existing sections renumbered 8–13.

---

## [0.2.37] — 2026-05-22

### Changed

**Starter kits — stub hardening (M27)**
- **`ApiAuth._resolve_user()`** now raises `NotImplementedError` instead of returning `None`. Previously the stub silently rejected every token with a 401; now it raises with a message pointing developers to `app/middleware/api_auth.py`, making unimplemented auth immediately visible rather than silently blocking all traffic.
- **`BillingController.webhook()`** now enforces Stripe signature verification. The method checks for `STRIPE_WEBHOOK_SECRET` in the environment (returns 500 with a config error if absent), calls `stripe.Webhook.construct_event()` to verify the `Stripe-Signature` header (returns 400 on failure), and returns 200 only for verified events. Previously the stub accepted all webhook payloads without any verification.

---

## [0.2.36] — 2026-05-22

### Added

**Scaffolding (M20)**
- **`hunt make:crud <Name> --fields="col:type ..."`** — single command that generates a model, timestamped migration (with typed columns), resourceful controller (index/create/store/show/edit/update/destroy), four Tailwind-styled Blade views (index, create, edit, show), and appends CRUD routes to `routes/web.py`. Running the command a second time for the same name skips route appending rather than duplicating.
- **`hunt make:api <Name> --fields="col:type ..."`** — generates a model, migration, API controller (JSON responses, no views), an `ApiResource`-style transformer class, and appends REST routes to `routes/api.py`.
- **`hunt.console.commands.make.field_types`** — shared utility used by both scaffold commands: `parse_fields()` parses `"col:type ..."` strings; `migration_columns()` renders Blueprint column lines; `fillable_list()` renders a Python list literal. Supported short-hands: `string`/`str`, `text`, `int`/`integer`, `bigint`, `smallint`, `float`, `decimal`, `bool`/`boolean`, `timestamp`, `date`, `json`, `uuid` — unknown types default to `string`.

**UI Component Library (M21)**
- **`@component('name', {props})`** — self-closing directive that renders `resources/views/components/<name>.html` (app override) or `hunt/views/components/<name>.html` (built-in), passing all props as scoped variables via a Jinja2 `{% with %}` block. Supports both Python dict syntax (`{'key': val}`) and PHP-style array syntax (`['key' => val]`). Nested list/dict values work as props (e.g. `{'headers': ['A', 'B']}`).
- **Block form** — `@component('modal', {props})` … `@endcomponent` captures body content as `_slot_default` and named `@slot('name')` … `@endslot` blocks as `_slot_<name>` variables, all accessible inside the component template.
- **10 built-in Tailwind components** in `hunt/views/components/`:
  - `alert` — success / error / warning / info variants with optional `title`
  - `badge` — pill badge with red / green / blue / yellow / purple / gray colors
  - `button` — primary / secondary / danger / ghost variants, `disabled` support
  - `card` — white card with optional `title`, `subtitle`, body, and `footer` slot
  - `table` — responsive table from `headers` (list) + `rows` (list of lists or dicts); graceful empty state
  - `modal` — `<dialog>` element with `id`, `title`, body, and `footer` slot
  - `navbar` — responsive top bar with `brand`, `links` list, and default slot for extra actions
  - `sidebar` — fixed-width sidebar with `links` list (supports `active`, `icon`), `brand`, and footer slot
  - `empty-state` — centered empty-state with optional icon, `description`, and CTA link
  - `form-group` — label + input/textarea/select combo with inline validation errors; supports `required`, `placeholder`, `options`
- **`hunt make:component <Name>`** — creates `resources/views/components/<name>.html` stub; uses PascalCase → kebab-case naming (e.g. `UserCard` → `user-card.html`).
- **`hunt vendor:publish --tag=components`** — copies all 10 built-in components into `resources/views/components/` for customisation; respects `--force` to overwrite existing files.

**Form Requests (M22)**
- **`FormRequest.validated()` now returns only declared fields** — previously returned all raw request data; now filters to the top-level keys present in `rules()`, providing mass-assignment safety. Nested rules (`'address.city'`) retain the full top-level key (`address`).
- **`FormRequest.after_validation(validator)`** — hook called after validation passes; override in subclasses to add cross-field checks or secondary validation without writing a `try/except` in the controller.
- **`FormRequest.input(key, default=None)`** — delegates to the underlying `Request.input()` for reading individual values without calling `validated()`.
- **`FormRequest.all()`** — returns all raw request input (unvalidated); delegates to `Request.all()`.
- **`FormRequest.file(key)`** — delegates to `Request.file()` for uploaded files.
- **`hunt make:form <Name>`** — alias for `make:request`; produces the same `FormRequest` subclass stub in `app/requests/`.
- **`@old('field')` directive** — expands to `{{ old('field') }}`; restores previous input after a failed form submit. Accepts an optional default: `@old('email', '')`.
- **`@errors` directive** — expands to a Tailwind-styled error-summary `<div>` that lists all validation messages; resolves as a no-op when `errors` is empty or undefined.

**App Introspection CLI (M23)**
- **`hunt route:list --json`** — new `--json` flag outputs all registered routes as a JSON array with `method`, `uri`, `name`, `action`, and `middleware` fields; plain output unchanged.
- **`hunt db:status [--json]`** — lists every migration file with its run/pending status; `--json` outputs a JSON array of `{"migration": str, "ran": bool}` objects.
- **`hunt config:show [key] [--json] [--no-redact]`** — displays resolved config values from `config/`; sensitive keys (`password`, `secret`, `key`, `token`, etc.) are redacted by default; `--no-redact` shows raw values; optional `key` argument filters to a specific key or namespace (e.g. `app` or `app.name`).
- **`hunt app:info [--json]`** — shows a full application summary: framework version, Python version, env/debug/URL settings, counts of routes/models/controllers/middleware/providers/jobs/migrations (ran vs pending), and active driver names for database/session/queue/cache/mail.

**Starter Kits (M24)**
- **`hunt new <name> --starter=<kit>`** — new `--starter` flag overlays a pre-built kit on the base skeleton after creation; accepts `blog`, `api`, or `saas`.
- **`blog` starter** — generates `Post` / `Category` / `Tag` models + migrations + factories; `PostController` with full CRUD; 4 Tailwind views (index, show, create, edit); `PostResource` and `CategoryResource` admin resources with dashboard metrics; blog-aware `layout.html` (nav with Posts / Login / Register); routes for all post actions wired by name.
- **`api` starter** — versioned `UserController` at `app/controllers/api/v1/`; `UserResource` JSON transformer with `to_array()` + `collection()`; `ApiAuth` Bearer-token middleware stub; `ApiRateLimit` (60 req/min); `/api/v1` route group with OpenAPI-style route docstring.
- **`saas` starter** — `Team` + `Membership` models; `BillingController` stub (plan selector, Stripe placeholders, webhook endpoint); `TenantMiddleware` for subdomain-based tenancy; `TeamResource` admin with plan metrics (Pro / Enterprise counts); `teams` migration with `plan`, `stripe_customer_id`, `stripe_subscription_id` columns.
- Each starter ships a **`README.md`** inside the generated app explaining what was created and how to run it.

**Live Reload Dev Server (M25)**
- **`hunt serve --reload`** now passes targeted `--reload-dirs` to Uvicorn covering only `app/`, `config/`, `resources/`, and `routes/` (directories that exist in the project); previously passed reload to Uvicorn without any directory scoping.
- **`hunt serve --open`** — opens `http://{host}:{port}` in the default browser 1.5 seconds after the server starts (non-blocking; uses a daemon thread).
- **Debug watch message** — when `APP_DEBUG=true` and `--reload` is active, the startup output now prints `Watching for changes in: <dirs>` so agents and developers can confirm which directories are being watched.

---

## [0.2.30] — 2026-05-20

### Added

**Schema builder**
- **`Blueprint.drop_column_if_exists(*names)`** — drops a column only when it exists, making migration `down()` methods idempotent after partial failures. On PostgreSQL and MySQL emits `DROP COLUMN IF EXISTS`; on SQLite (which has no such syntax) checks `PRAGMA table_info` first and skips the statement if the column is absent.

**Admin panel**
- **`DateRangeFilter`** (`hunt.admin.filter`) — renders two `<input type="date">` fields in the index toolbar (`filter_<slug>_from` / `filter_<slug>_to` query params). `AdminResource.apply_filters()` handles the two-param convention automatically.
- **`RestoreAction`** — built-in bulk action that calls `restore()` on each selected record; compatible with any resource using the `SoftDeletes` mixin.
- **`ExportCsvAction`** — built-in bulk action that generates a CSV from selected records and returns it as a file download (`Content-Disposition: attachment`). Subclass and set `filename` to customise the downloaded filename.
- **`ActionResponse.download(content, filename, content_type)`** — new factory for file-download results; the action controller serves the content directly with appropriate headers instead of redirecting.
- **`hunt admin:publish`** — copies all admin templates from the package into `resources/views/admin/` for customisation. The admin renderer already prefers app-level templates over package templates. Accepts `--force` to overwrite existing files.
- `DateRangeFilter`, `RestoreAction`, and `ExportCsvAction` exported from `hunt.admin`.

**Two-factor authentication (TOTP)**
- `TwoFactor` service (`hunt.auth.two_factor`) — `generate_secret()`, `qr_code_url()`, `verify()` (±1 window, strips spaces), `generate_recovery_codes(n=8)`.
- `_SessionGuard.attempt()` now detects `two_factor_enabled=True` on the resolved user; stores `_2fa_pending = user_id` in session and returns `False` instead of logging in.
- `_SessionGuard.two_factor_pending()` — convenience predicate for redirect logic in login controllers.
- Built-in controllers in `hunt.auth.controllers.two_factor`: `TwoFactorSetupController` (password confirm → QR → TOTP confirm → enable), `TwoFactorChallengeController` (TOTP or recovery-code login), `TwoFactorManageController` (view/regenerate recovery codes, disable 2FA).
- `EnsureTwoFactorAuthenticated` middleware (`hunt.http.middleware.two_factor`) — redirects any session with `_2fa_pending` to `/two-factor/challenge`; exempts `/login`, `/logout`, and `/two-factor/challenge`.
- `hunt make:2fa-controllers` — scaffolds routes, controller stub, Tailwind-styled templates, and a migration adding `two_factor_secret`, `two_factor_enabled`, `two_factor_recovery_codes` columns to the `users` table.
- `pyotp>=2.9,<3.0` added as a framework dependency.

**Routing**
- `Router.domain(pattern)` — constrains a route group to requests where the `Host` header matches a static (`"api.example.com"`) or parameterised (`"{account}.example.com"`) pattern.
- `Route._domain` — optional domain pattern; `Route.matches()` now accepts `host=` and enforces the domain constraint when set. Domain and path parameters are merged into a single params dict.
- `Request.host` — lowercased `Host` header value with port stripped.
- `Request.subdomain(root_domain)` — returns the subdomain prefix relative to a root domain.
- `Router.dispatch()` now accepts `host=` and passes it to `route.matches()`.

**Queue dashboard**
- `jobs_history` table — generated by `hunt queue:table`; records every completed and permanently failed job with `job_class`, `queue`, `duration_ms`, `finished_at`, and `status`.
- Queue worker now writes to `jobs_history` on job completion and final failure (optional — silently skipped if the table is absent).
- Queue admin dashboard: 24-hour throughput bar chart (green = completed / red = failed) and per-queue breakdown table (pending, processed last hour, failed last hour).
- Failed job **View** modal — `<dialog>` showing full exception text and pretty-printed payload JSON, with a Retry button.

**Debug panel**
- `DebugPanel` middleware (`hunt.http.middleware.debug_panel`) — injects a fixed-position debug toolbar into every `text/html` response when `APP_DEBUG=true`. Four tabs: **Request**, **Route**, **Queries** (SQL log with per-query timing), **Session**.
- `debug.get_query_log()` — returns the per-request query log as `(sql, elapsed_ms)` tuples.
- `reset_query_tracker()` now also resets the per-request query log.
- Matched route info (`_debug_route_name`, `_debug_route_uri`) stored on `Request` by the kernel.

**Session / Redis**
- `RedisSessionStore` — Redis-backed session store activated via `SESSION_DRIVER=redis`. Sessions stored as `hunt:session:<id>` keys with TTL-based expiry.
- `hunt.redis_connection.get_redis()` — shared Redis client factory configured via `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, and `REDIS_PASSWORD`.
- `StartSession` middleware reads `SESSION_DRIVER` and instantiates the correct store automatically.

**API resources**
- `ApiResource` — base class for transforming model instances into JSON. Return one from any controller; the kernel calls `to_response()` automatically.
- `ApiResource.when(condition, value, default=<omit>)` — conditionally includes a key.
- `ApiResource.merge_when(condition, data)` — conditionally spreads a dict of attributes.
- `ApiResource.collection(items)` / `ApiResourceCollection` — wraps a list or `PaginationResult` in `{"data": [...]}`, appending a `"meta"` key when given a `PaginationResult`.
- Importable from `hunt.http`: `from hunt.http import ApiResource, ApiResourceCollection`.

**Pagination**
- `PaginationResult` — `paginate()` now returns a `PaginationResult` object. Backwards-compatible: attribute access, item access, and iteration all work.
- `PaginationResult.links(base_url)`, `prev_page_url()`, `next_page_url()` — URL helpers that preserve existing query parameters.
- `QueryBuilder.simple_paginate(per_page, page)` — paginates without a `COUNT(*)` query.
- `pagination.html` Jinja2 macro — Tailwind-styled pagination bar.

**Soft deletes**
- `SoftDeletes` mixin: add `SoftDeletes` before `Model` to opt in to soft-delete behaviour.
- `QueryBuilder.only_trashed()` — restricts results to soft-deleted rows (`deleted_at IS NOT NULL`).

### Fixed

- **`_safe_default()` now emits `true`/`false` for PostgreSQL boolean defaults** — previously always emitted `1`/`0` regardless of dialect, which PostgreSQL rejects for boolean columns. The fix is applied in both `Blueprint._column_sql()` (table creation and `ADD COLUMN`) and `_pg_alter_column()` (ALTER COLUMN SET DEFAULT). SQLite and MySQL continue to use `1`/`0`.
- `RedisDriver.pop()` — was always returning `attempts: 1`. Attempt counts now tracked in a Redis hash using `HINCRBY`.
- `RedisDriver.fail()` — was writing failed jobs to a Redis sorted set the admin panel could not read. Failed jobs now go to the `jobs_failed` database table, consistent with the database driver.
- `RedisDriver.delete()` — now cleans up the attempt hash entry on successful job completion.

---

## [0.2.19] — 2026-05-19

### Added

**Database & ORM**
- Query debug logging — all SQL queries logged at `DEBUG` level when `APP_DEBUG=true`, including SQL text, bindings, and elapsed time in milliseconds.
- N+1 detector — when `APP_DEBUG=true`, the same normalised query pattern triggers a `WARNING` after 10 executions per request.
- Connection pool configurable via env vars: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`. SQLite uses a single-connection pool and ignores these.
- Query scopes — define `scope_<name>(cls, query)` on a Model and call it fluently: `Post.query().published().get()`.
- `Model.increment(col, amount=1)` / `decrement(col, amount=1)` — update DB and in-memory attribute in one call.
- `Model.first_or_new(search, attributes)` — like `first_or_create` but returns an unsaved instance.
- `Model.attributes` — default column values applied to every new unsaved instance.
- `Model.create_many(rows)` / `QueryBuilder.insert_many(rows)` — bulk-insert a list of dicts in a single SQL statement.
- `Blueprint.foreign(col).references(col).on(table).on_delete(action).on_update(action)` — FK constraints with `CASCADE`, `SET NULL`, `RESTRICT`, `NO ACTION`, `SET DEFAULT`.
- `QueryBuilder.where_group(callback)` / `or_where_group(callback)` — wrap sub-queries in parentheses for correct AND/OR precedence.
- `QueryBuilder.min(col)`, `max(col)`, `avg(col)`, `sum(col)` — standard SQL aggregates.
- `QueryBuilder.pluck(col)` — returns a flat list of values for a single column.
- `QueryBuilder.only(*columns)` — alias for `select(*columns)`.
- `QueryBuilder.without(*columns)` — selects all columns except the specified ones.
- `ILIKE` / `NOT ILIKE` added to the operator allowlist (PostgreSQL case-insensitive LIKE).
- Nested validation — dotted paths (`"address.city"`) and wildcard paths (`"items.*.name"`) work in validator rules.
- `required_with:field1,field2` / `required_without:field1,field2` validation rules.
- Route model binding — annotate a route parameter with a `Model` subclass and hunt auto-resolves it via `find_or_fail`; 404 on missing record.
- Route regex constraints — `{id:\d+}` syntax restricts what a route segment matches.
- Implicit `OPTIONS` handling — unmatched `OPTIONS` requests return `204` through global middleware with an `Allow` header.
- Wrong-method requests return `405 Method Not Allowed` with an `Allow` header instead of `404`.
- `Response.with_etag()`, `last_modified()`, `cache()`, `no_cache()` — cache/validation header helpers.

**Auth & session**
- `Auth.login_using_id(id)`, `Auth.once(credentials)`, `Auth.once_using_id(id)`.
- Configurable `username` field on session guards via `Auth.configure()`.
- `FileSessionStore.pull(key, default=None)` — get and remove in one call.
- `FileSessionStore.remember(key, callback)` — get or compute and cache.
- Configurable session lifetime via `config/session.py` and `SESSION_LIFETIME` env var.

**Queue & jobs**
- Job timeout enforcement — `Job.timeout` serialized into the payload and enforced by the worker (`SIGALRM` on Unix, daemon thread on Windows).
- `Job.name` class attribute — short name for CLI identification.
- `hunt job:list` — scans `app/jobs/` and prints a table of every discovered `Job` subclass.
- `hunt job:run <name>` — runs a job synchronously; accepts `--data key=value` args (JSON-decoded).
- Queue monitor page at `/hunt-admin/queue` — pending, processing, failed jobs with retry / delete / flush actions.

**Admin panel**
- `BelongsTo` fields batch-loaded on the index page — one query per relation, not per row.
- Non-searchable `BelongsTo` renders as a `<select>` on create/edit forms.
- Searchable `BelongsTo` renders as a text-input autocomplete against `GET /hunt-admin/resources/{key}/search-relation`.
- `BulkDeleteAction` — built-in bulk delete with confirmation dialog.
- `AuditLog` mixin — records create / update / delete events to `admin_audit_logs`; "History" tab on detail page.
- Custom stubs — all `hunt make:*` commands check `stubs/<name>.stub` before using built-in templates.

**Mail & notifications**
- `Mailable.with_mailer(name)`, `attach_data(data, name, mime_type)`.
- `Mail.to(...).cc(address)`, `.bcc(address)`, `.later(delay, mailable)`.
- `NotificationFake` context-manager fake with `assert_sent_to`, `assert_not_sent_to`, etc.
- Slack notification channel via `SlackMessage` fluent builder and incoming webhooks.
- Queueable notifications — `should_queue = True` on a `Notification` class.
- `notifiable.mark_notification_read(notification_id)`.

**Cache & storage**
- Redis cache driver (`CACHE_DRIVER=redis`).
- `Cache.add()`, `Cache.pull()`, `Cache.get_many()`, `Cache.put_many()`.
- `Storage.path()`, `size()`, `last_modified()`, `mime_type()`, `copy()`, `move()`, `append()`, `prepend()`, `files()`, `all_files()`, `directories()`, `make_directory()`, `delete_directory()` — all proxied to the default disk.

**Logging**
- `daily` log channel — rotates at midnight.
- `stderr` log channel — writes to stderr.
- `stack` log channel — fans out to multiple named channels.
- `Log.channel(name)` — select a specific named channel.
- Multi-channel `Log.configure()` with `channels=` dict and `default=`.

**Developer experience**
- `dump(*values)` — pretty-prints and returns last value.
- `dd(*values)` — pretty-prints and raises `SystemExit(0)`.
- `env()` now coerces `"true"` / `"false"` to `bool`, numeric strings to `int` / `float`.
- `old()` in templates is now callable: `{{ old("field") }}` with optional default.
- `app/console/kernel.py` scaffold — app-level commands loaded into the `hunt` CLI automatically.
- `hunt upgrade` adds `app/console/kernel.py` to existing applications.
- `hunt make:command` auto-registers the generated command in `app/console/kernel.py`.

**Testing**
- `DatabaseTransactions` test mixin — wraps each test in a rolled-back transaction.
- `MailFake` — context-manager fake with `assert_sent`, `assert_not_sent`, `assert_sent_to`, `assert_nothing_sent`.
- `TestResponse.assert_cookie()`, `assert_json_count()`, `assert_json_missing()`, `assert_json_fragment()`.
- Query string support in test client — `self.get("/posts?page=2")` correctly populates `request.query_string`.

### Fixed

- `Model.timestamps = False` per-instance now correctly disables timestamp tracking without mutating the class attribute.
- Session files garbage-collected on ~1% of requests, preventing unbounded growth of the session directory.
- `hunt --version` now reads from `hunt.__version__` instead of installed package metadata.
- `QueryBuilder.count()` no longer forwards `ORDER BY`, `LIMIT`, or `OFFSET` into the count query — PostgreSQL rejects `ORDER BY` on an aggregate without `GROUP BY`.

---

## [0.2.3] — 2026-05-18

### Fixed

- Schema builder now generates correct DDL for MySQL and PostgreSQL — previously all DDL was SQLite-specific.
- `AUTOINCREMENT` replaced with `AUTO_INCREMENT` (MySQL) and `GENERATED ALWAYS AS IDENTITY` (PostgreSQL).
- MySQL-specific column types mapped to their PostgreSQL equivalents instead of being sent verbatim.
- `UNSIGNED` modifier emitted for MySQL; silently omitted on PostgreSQL and SQLite.
- `CREATE INDEX IF NOT EXISTS` replaced with `CREATE INDEX` on MySQL, which does not support the `IF NOT EXISTS` clause.

---

## [0.2.0] — 2026-05-14

### Added

**Admin panel**
- Full admin panel at `/hunt-admin` with resource CRUD, index filtering, sorting, and pagination.
- Field types: `Text`, `Email`, `Password`, `Textarea`, `RichText`, `Number`, `Boolean`, `Select`, `Image`, `DateTime`, `BelongsTo`, `HasMany`, `Badge`.
- Fluent field API: `.sortable()`, `.readonly()`, `.rules()`, `.hide_from_forms()`, `.show_on_index()`, `.hide_from_index()`, etc.
- Filter system with `SelectFilter`, `BooleanFilter`, `TrashedFilter`.
- Action system with `ActionResponse.success()`, `ActionResponse.error()`, `ActionResponse.redirect()`.
- Metric cards: `ValueMetric`, `TrendMetric`, `PartitionMetric`.
- Global search across all registered resources.
- Navigation API: `NavGroup`, `NavResource`, `NavLink` with auto-generated sidebar.
- `hunt make:admin-resource` — scaffolds and auto-registers an `AdminResource`.

**Authentication**
- Session-backed auth via `Auth.attempt()`, `Auth.login()`, `Auth.logout()`, `Auth.user()`, `Auth.check()`.
- Full login / registration / forgot-password / reset-password scaffold via `hunt new`.
- Auth feature flags in `config/auth.py` (`registration`, `login`, `forgot_password`).
- Session ID regenerated on login to prevent session fixation.

**CLI**
- `hunt new` — scaffold a complete application with auth, admin, migrations, and config files.
- `hunt upgrade` — add missing scaffold files to existing projects; shows unified diff for locally modified files.
- `hunt schedule:list` / `hunt schedule:run`.
- `hunt queue:work` / `hunt queue:failed` / `hunt queue:flush` / `hunt queue:retry`.
- Full `make:*` suite: `model`, `controller`, `migration`, `middleware`, `event`, `listener`, `job`, `mail`, `seeder`, `factory`, `command`, `request`, `resource`, `rule`, `policy`, `observer`, `notification`, `admin-resource`.

**Queue system**
- Sync, database, and Redis queue drivers.
- CAS-based pop in the database driver prevents race conditions under concurrent workers.
- Job chaining, delayed dispatch, configurable retries and backoff.
- `queue:work --once` for single-job processing.

**Scheduler**
- Cron-expression scheduler with `every_minute()`, `hourly()`, `daily()`, `weekly()`, `monthly()`, and arbitrary `.cron()` expressions.
- Constraint modifiers: `.environments()`, `.when()`, `.skip()`, `.between()`.
- Background execution, output capture, lifecycle hooks, health-check pings.

**Templates & views**
- hunt template syntax: `@extends`, `@section`, `@yield`, `@include`, `@foreach`, `@if`, `@auth`, `@guest`, `@csrf`, `@error`, `@env`.
- `ViewFactory` injects `config()`, `csrf_token`, `auth_user`, `request`, `can()`, and flash data into every template.
- View composers and shared variables.

**Security**
- CSRF protection middleware with per-session tokens.
- Static file server blocks dangerous extensions: `.py`, `.env`, `.sh`, `.svg`, `.php`, and more.
- `Image` field excludes SVG from allowed upload MIME types by default.
- bcrypt password hashing via `hash_password()`.
- HTML output auto-escaped; raw output requires explicit `{!! !!}` syntax.

**Other**
- ORM with `where`, `or_where`, `order_by`, `limit`, `paginate`, `first_or_create`, `find_or_fail`, soft deletes, relationships (`has_one`, `has_many`, `belongs_to`).
- Event/listener system with `Dispatcher.dispatch_sync()` and the `event()` helper.
- Validation with 15+ built-in rules including `unique`, `confirmed`, `regex`, `in`.
- Storage system with local and S3 drivers; `storage:link` command.
- Cache system with file and Redis drivers.
- Mail system with SMTP and log transports.
- Translation / localisation support.
- `hunt tinker` — interactive REPL with full application context.

### Fixed

- `make:admin-resource` normalises PascalCase model names to the correct lowercase filename.
- Scaffolded `layout.html` uses `config('key', default)` function syntax (not `.get()`).

---

[Unreleased]: https://github.com/hunt-core/hunt/compare/v0.2.30...HEAD
[0.2.30]: https://github.com/hunt-core/hunt/compare/v0.2.19...v0.2.30
[0.2.19]: https://github.com/hunt-core/hunt/compare/v0.2.3...v0.2.19
[0.2.3]: https://github.com/hunt-core/hunt/compare/v0.2.0...v0.2.3
[0.2.0]: https://github.com/hunt-core/hunt/releases/tag/v0.2.0

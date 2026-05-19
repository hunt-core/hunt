# Changelog

All notable changes to hunt are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
hunt uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.2.16] — 2026-05-19

### Added

**Mail**
- **`Mailable.with_mailer(name)`** — send a single mailable through a specific named mailer (e.g. `WelcomeEmail().with_mailer("postmark")`) without changing the default
- **`Mailable.attach_data(data, name, mime_type)`** — attach raw bytes (or str) without writing a temporary file; useful for dynamically generated PDFs, CSVs, etc.
- **`Mail.to(...).cc(address)`** and **`.bcc(address)`** — fluent CC/BCC before sending
- **`Mail.later(delay, mailable)`** and **`Mail.to(...).later(delay, mailable)`** — queue a mailable with a delay in seconds
- **`NotificationFake`** — context-manager fake that intercepts `notifiable.notify()`; assertions: `assert_sent_to`, `assert_not_sent_to`, `assert_nothing_sent`, `sent(cls)`

**Notifications**
- **Slack channel** — send notifications via Slack incoming webhooks; implement `to_slack(notifiable)` returning a `SlackMessage` and add `"slack"` to `via()`
- **`SlackMessage` fluent builder** — `content()`, `block()`, `attachment()`, `from_()`, `icon()`, `channel()`, `to(webhook_url)`
- **Queueable notifications** — set `should_queue = True` on a `Notification` class to automatically dispatch each channel through the queue
- **`notifiable.route_notification_for_slack()`** — override on notifiable to provide a per-entity webhook URL
- **`notifiable.mark_notification_read(notification_id)`** — mark a single database notification as read by its UUID

---

## [0.2.15] — 2026-05-19

### Added

**Cache**
- **Redis cache driver** — `CACHE_DRIVER=redis` now works; requires `pip install redis`; honours `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` from env
- `Cache.add(key, value, seconds)` — store only if the key is not already present; returns `True` if written, `False` otherwise
- `Cache.pull(key, default=None)` — get and remove a cache entry in one call
- `Cache.get_many(keys)` — batch-get; returns `{key: value}` dict (`None` for missing keys)
- `Cache.put_many(values, seconds)` — batch-put a dict of key/value pairs

**Storage**
- `Storage.path()`, `Storage.size()`, `Storage.last_modified()`, `Storage.mime_type()` — convenience proxies now available directly on `Storage` (previously only on `Storage.disk()`)
- `Storage.copy()`, `Storage.move()`, `Storage.append()`, `Storage.prepend()`, `Storage.files()`, `Storage.all_files()`, `Storage.directories()`, `Storage.make_directory()`, `Storage.delete_directory()` — same; all proxy to the default disk

**Logging**
- **`daily` log channel** — rotates at midnight, keeps configurable number of days; set `driver: "daily"` in channel config
- **`stderr` log channel** — writes to stderr only (good for containers and systemd); `driver: "stderr"`
- **`stack` log channel** — fans out to multiple named channels simultaneously; `driver: "stack", channels: ["file", "stderr"]`
- **`Log.channel(name)`** — select a specific named channel: `Log.channel("stderr").error("...")`
- **Multi-channel `Log.configure()`** — pass `channels={"file": {...}, "daily": {...}}` and `default="stack"` for full multi-channel setup

---

## [0.2.14] — 2026-05-19

### Added

- **`Auth.login_using_id(id)`** — fetch a user by primary key and log them in; returns the user instance or `None`
- **`Auth.once(credentials)`** — validate credentials and authenticate the user for the current request only; the session is never written
- **`Auth.once_using_id(id)`** — authenticate a specific user for the current request only without touching the session
- **Configurable `username` field on session guards** — pass `"username": "phone"` (or any column) in `Auth.configure()` guard config; defaults to `"email"`
- **`FileSessionStore.pull(key, default=None)`** — get a session value and remove it in one call
- **`FileSessionStore.remember(key, callback)`** — return existing value if present, otherwise call `callback`, store the result, and return it
- **Configurable session lifetime** — `config/session.py` `session.lifetime` (seconds) now controls both the session file expiry and the cookie `Max-Age`; defaults to `7200`; new `hunt new` scaffolds `config/session.py`; `SESSION_LIFETIME` env var supported out of the box

---

## [0.2.13] — 2026-05-19

### Added

- **`DatabaseTransactions` test mixin** — wraps each test in a database transaction that is rolled back on teardown; faster than `RefreshDatabase` for large tables
- **`MailFake`** — context-manager fake that records sent mail; assertions: `assert_sent`, `assert_not_sent`, `assert_sent_to`, `assert_nothing_sent`
- **`TestResponse.assert_cookie(name, value=None)`** — asserts a `Set-Cookie` header was returned with the given name and optional value
- **`TestResponse.assert_json_count(key, count)`** — asserts a JSON array (top-level or under `key`) has exactly `count` items
- **`TestResponse.assert_json_missing(key)`** — asserts a JSON key is absent from the response
- **`TestResponse.assert_json_fragment(fragment)`** — asserts a dict subset matches the JSON response
- **Query string support in test client** — `self.get("/posts?page=2")` now correctly populates `request.query_string`; previously the query string was silently dropped

---

## [0.2.12] — 2026-05-19

### Added

- **Route model binding** — annotate a route parameter with a `Model` subclass and hunt auto-resolves it via `find_or_fail`: `async def show(request, post: Post)` → `post` is the loaded model, 404 on missing
- **Route regex constraints** — `{id:\d+}` syntax restricts what a route segment matches; `{slug:[a-z-]+}` etc.
- **Implicit OPTIONS handling** — `OPTIONS` requests that match no explicit route now return `204` through global middleware (so `HandleCors` applies CORS headers automatically); includes `Allow` header listing registered methods for the path
- `Response.with_etag(etag)` — sets `ETag` header (auto-quotes bare values)
- `Response.last_modified(dt)` — sets `Last-Modified` header; accepts `datetime` or RFC 7231 string
- `Response.cache(seconds, public=True)` — sets `Cache-Control: public, max-age=N`
- `Response.no_cache()` — sets `Cache-Control: no-store, no-cache, must-revalidate` + `Pragma: no-cache`

---

## [0.2.11] — 2026-05-19

### Added

- **Nested validation** — dotted paths (`"address.city"`) and wildcard paths (`"items.*.name"`) now work in validator rules; errors are reported under the full path key (e.g. `"items.0.name"`)
- `required_with:field1,field2` — field is required when any of the listed fields are present and non-empty
- `required_without:field1,field2` — field is required when any of the listed fields are absent or empty

---

## [0.2.10] — 2026-05-19

### Added

- **Query scopes** — define `scope_<name>(cls, query)` on a Model and call it fluently: `Post.query().published().get()`
- `Model.increment(col, amount=1)` / `decrement(col, amount=1)` — instance-level shortcuts that update the DB and the in-memory attribute in one call
- `Model.first_or_new(search, attributes)` — like `first_or_create` but returns an unsaved instance instead of persisting
- `Model.attributes` class variable — default column values applied to every new (unsaved) instance
- `Model.create_many(rows)` / `QueryBuilder.insert_many(rows)` — bulk-insert a list of dicts in a single SQL statement
- `Blueprint.foreign(col).references(col).on(table).on_delete(action).on_update(action)` — FK constraints now emit `FOREIGN KEY ... REFERENCES ... ON DELETE ... ON UPDATE ...` in `CREATE TABLE`; supported actions: `CASCADE`, `SET NULL`, `RESTRICT`, `NO ACTION`, `SET DEFAULT`

---

## [0.2.9] — 2026-05-19

### Added

- `QueryBuilder.where_group(callback)` / `or_where_group(callback)` — wrap a sub-query in parentheses for correct AND/OR precedence (e.g. `WHERE (a=1 OR b=2) AND (c=3 OR d=4)`)
- `QueryBuilder.min(col)`, `max(col)`, `avg(col)`, `sum(col)` — standard SQL aggregates alongside the existing `count()`
- `QueryBuilder.pluck(col)` — returns a flat `list` of values for a single column
- `ILIKE` / `NOT ILIKE` added to the SQL operator allowlist (PostgreSQL case-insensitive LIKE)

### Fixed

- `Model.timestamps = False` per-instance now correctly disables timestamp tracking without mutating the class attribute; `__setattr__` now checks the full MRO so inherited config attributes are stored on the instance
- Session files are now garbage-collected on ~1% of requests, preventing unbounded growth of the session directory

---

## [0.2.8] — 2026-05-19

### Fixed

- `hunt --version` now reads from `hunt.__version__` instead of installed package metadata, so it always reflects the running source version

---

## [0.2.7] — 2026-05-19

### Added

- `app/console/kernel.py` scaffold file with a `register(cli)` function — app-level commands are now loaded into the `hunt` CLI automatically at startup
- `hunt upgrade` adds `app/console/kernel.py` to existing applications that don't have it
- `hunt make:command` now auto-registers the generated command in `app/console/kernel.py`

---

## [0.2.6] — 2026-05-19

### Added

- `Job.name` class attribute — optional short name used by the CLI to identify jobs without requiring a full dotted class path
- `hunt job:list` — scans `app/jobs/` and prints a table of every discovered `Job` subclass with its name, class path, queue, and tries
- `hunt job:run <name>` — runs a job synchronously; `name` can be the short `name` attribute, the class name, or a full dotted path (`app.jobs.my_job.MyJob`); accepts `--data key=value` for constructor arguments (values are JSON-decoded so integers, booleans, and arrays work)
- `make:job` scaffold now includes the `name` attribute pre-filled with the snake_case job name

---

## [0.2.5] — 2026-05-19

### Added

- Queue monitor page at `/hunt-admin/queue` — lists pending, processing, and delayed jobs with status badges; shows failed jobs with truncated exception details and retry / delete / flush-all actions
- "System" navigation group auto-added to admin sidebar with a Queue link

---

## [0.2.4] — 2026-05-19

### Fixed

- `QueryBuilder.count()` no longer forwards `ORDER BY`, `LIMIT`, or `OFFSET` into the count query — PostgreSQL (correctly) rejects `ORDER BY` on an aggregate without a `GROUP BY`, causing paginated admin index pages to crash on non-SQLite backends

---

## [0.2.3] — 2026-05-18

### Fixed

- Schema builder now generates correct DDL for MySQL and PostgreSQL — previously all DDL was SQLite-specific
- `AUTOINCREMENT` keyword replaced with `AUTO_INCREMENT` (MySQL) and `GENERATED ALWAYS AS IDENTITY` (PostgreSQL) for auto-increment primary keys
- MySQL-specific column types (`LONGTEXT`, `MEDIUMTEXT`, `TINYINT`, `DATETIME`, `DOUBLE`, `BLOB`, `FLOAT(p,s)`) are now mapped to their PostgreSQL equivalents (`TEXT`, `TEXT`, `SMALLINT`, `TIMESTAMP`, `DOUBLE PRECISION`, `BYTEA`, `REAL`/`DOUBLE PRECISION`) instead of being sent verbatim and rejected
- `UNSIGNED` modifier on integer columns now emitted for MySQL; silently omitted on PostgreSQL and SQLite where it is unsupported
- `CREATE INDEX IF NOT EXISTS` replaced with `CREATE INDEX` on MySQL, which does not support the `IF NOT EXISTS` clause for index creation

---

## [0.2.0] — 2026-05-14

### Added

**Admin panel**
- Full admin panel at `/hunt-admin` with resource CRUD, index filtering, sorting, and pagination
- Field types: `Text`, `Email`, `Password`, `Textarea`, `RichText`, `Number`, `Boolean`, `Select`, `Image`, `DateTime`, `BelongsTo`, `HasMany`, `Badge`
- Fluent field API: `.sortable()`, `.readonly()`, `.rules()`, `.hide_from_forms()`, `.show_on_index()`, `.hide_from_index()`, etc.
- Filter system with `SelectFilter`, `BooleanFilter`, `TrashedFilter`; options accept both dict and tuple formats
- Action system with `ActionResponse.success()`, `ActionResponse.error()`, `ActionResponse.redirect()`
- Metric cards: `ValueMetric`, `TrendMetric`, `PartitionMetric`
- Global search across all registered resources, results grouped by resource with "View all" links
- Navigation API: `NavGroup`, `NavResource`, `NavLink` with backward-compatible auto-generated sidebar
- `hunt make:admin-resource` command — scaffolds and auto-registers an `AdminResource`; accepts both `Post` and `post` as the model argument

**Authentication**
- Session-backed auth via `Auth.attempt()`, `Auth.login()`, `Auth.logout()`, `Auth.user()`, `Auth.check()`
- Full login / registration / forgot-password / reset-password scaffold via `hunt new`
- Auth feature flags in `config/auth.py` (`registration`, `login`, `forgot_password`) — disabled features remove routes entirely and hide corresponding links in built-in auth views
- Session ID regenerated on login to prevent session fixation

**CLI**
- `hunt new` — scaffold a complete application with auth, admin, migrations, and config files; auto-generates `APP_KEY` in `.env`
- `hunt upgrade` — add missing scaffold files to existing projects; shows unified diff for locally modified files; creates new config files (e.g. `config/auth.py`) without overwriting customised ones
- `hunt schedule:list` / `hunt schedule:run` — list and run scheduled tasks
- `hunt queue:work` / `hunt queue:failed` / `hunt queue:flush` / `hunt queue:retry` — queue worker and failed job management
- Full `make:*` suite: `model`, `controller`, `migration`, `middleware`, `event`, `listener`, `job`, `mail`, `seeder`, `factory`, `command`, `request`, `resource`, `rule`, `policy`, `observer`, `notification`, `admin-resource`

**Queue system**
- Sync, database, and Redis queue drivers
- CAS-based pop in the database driver prevents race conditions under concurrent workers
- Job chaining, delayed dispatch, configurable retries and backoff
- `queue:work --once` for single-job processing

**Scheduler**
- Cron-expression scheduler with `every_minute()`, `hourly()`, `daily()`, `weekly()`, `monthly()`, and arbitrary `.cron()` expressions
- Constraint modifiers: `.environments()`, `.when()`, `.skip()`, `.between()`
- Background execution, output capture, lifecycle hooks (`.on_success()`, `.on_failure()`), health-check pings

**Templates & views**
- hunt template syntax: `@extends`, `@section`, `@yield`, `@include`, `@foreach`, `@if`, `@auth`, `@guest`, `@csrf`, `@error`, `@env`
- `ViewFactory` automatically injects `config()`, `csrf_token`, `auth_user`, `request`, `can()`, and flash data into every template
- View composers and shared variables

**Security**
- CSRF protection middleware with per-session tokens
- Static file server blocks dangerous extensions: `.py`, `.env`, `.sh`, `.svg`, `.php`, and more
- `Image` field excludes SVG from allowed upload MIME types by default
- bcrypt password hashing via `hash_password()`
- HTML output auto-escaped; raw output requires explicit `{!! !!}` syntax

**Other**
- ORM with `where`, `or_where`, `order_by`, `limit`, `paginate`, `first_or_create`, `find_or_fail`, soft deletes, relationships (`has_one`, `has_many`, `belongs_to`)
- Event/listener system with `Dispatcher.dispatch_sync()` and the `event()` helper
- Validation with 15+ built-in rules including `unique`, `confirmed`, `regex`, `in`
- Storage system with local and S3 drivers; `storage:link` command
- Cache system with file and Redis drivers
- Mail system with SMTP and log transports
- Translation / localisation support
- `hunt tinker` — interactive REPL with full application context

### Fixed
- `make:admin-resource` normalises PascalCase model names to the correct lowercase filename
- Scaffolded `layout.html` uses `config('key', default)` function syntax (not `.get()`)

---

[Unreleased]: https://github.com/hunt-core/hunt/compare/v0.2.16...HEAD
[0.2.16]: https://github.com/hunt-core/hunt/compare/v0.2.15...v0.2.16
[0.2.15]: https://github.com/hunt-core/hunt/compare/v0.2.14...v0.2.15
[0.2.14]: https://github.com/hunt-core/hunt/compare/v0.2.13...v0.2.14
[0.2.13]: https://github.com/hunt-core/hunt/compare/v0.2.12...v0.2.13
[0.2.12]: https://github.com/hunt-core/hunt/compare/v0.2.11...v0.2.12
[0.2.11]: https://github.com/hunt-core/hunt/compare/v0.2.10...v0.2.11
[0.2.10]: https://github.com/hunt-core/hunt/compare/v0.2.9...v0.2.10
[0.2.9]: https://github.com/hunt-core/hunt/compare/v0.2.8...v0.2.9
[0.2.8]: https://github.com/hunt-core/hunt/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/hunt-core/hunt/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/hunt-core/hunt/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/hunt-core/hunt/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/hunt-core/hunt/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/hunt-core/hunt/compare/v0.2.0...v0.2.3
[0.2.0]: https://github.com/hunt-core/hunt/releases/tag/v0.2.0

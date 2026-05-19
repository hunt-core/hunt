# Changelog

All notable changes to hunt are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
hunt uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/hunt-core/hunt/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/hunt-core/hunt/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/hunt-core/hunt/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/hunt-core/hunt/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/hunt-core/hunt/compare/v0.2.0...v0.2.3
[0.2.0]: https://github.com/hunt-core/hunt/releases/tag/v0.2.0

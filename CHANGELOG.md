# Changelog

All notable changes to hunt are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
hunt uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.4.20] — 2026-05-28

### Added

- **Boolean field toggle** — Boolean fields in the admin resource index table are now clickable toggles when the user has update permission. Clicking flips the value inline via `fetch()` (no page reload) through the new `POST /resources/{key}/{id}/field-toggle` endpoint.
- **`AdminResource.title_attribute`** — Resources can now set `title_attribute = "slug"` (or any column name) as a class variable to explicitly control the display title used in relationships, form selects, breadcrumbs, and the show-page heading. Falls back to the existing auto-discovery (`name` → `title` → `id`) when not set.
- **HasMany panel filter & sort** — Relationship panels on the resource show page now include a search input and sortable column headers. Sort/filter state is carried in URL query params (`rel_{attribute}_sort`, `rel_{attribute}_dir`, `rel_{attribute}_search`).

### Changed

- **BelongsTo values link in index table** — BelongsTo field cells in the resource index table are now rendered as links to the related record's show page (matching the existing behaviour on the detail page).
- **HasMany first-cell link** — The first data cell in each HasMany relationship panel is now a link to the related record's show page, in addition to the existing "View →" action column.
- **Sortable column icons** — Sortable columns in the resource index always show a bi-directional sort icon. The active sort column shows a directional ↑/↓ arrow in indigo.
- **Debug panel Redis resilience** — `RedisSessionStore._read()` and `save()` now catch connection errors silently; the debug panel Session tab also catches errors and displays "session unavailable" instead of crashing.

---

## [0.4.19] — 2026-05-27

### Added

- **Router `head()` and `options()` methods** — `Router` now exposes dedicated `head(uri, action)` and `options(uri, action)` registration helpers, completing coverage of all standard HTTP verbs. `Router.any()` also now includes `OPTIONS` in its method list.

---

## [0.4.18] — 2026-05-27

### Changed

- **Admin panel pagination** — the Logs, Routes, Failed Jobs, and Pending Jobs admin pages now paginate their results. Logs and Routes show 50 rows per page (with search/filter params preserved across pages); Failed Jobs and Pending Jobs each show 25 rows per page with independent pagination controls (SQL-level `LIMIT`/`OFFSET` for queue tables). A shared `_macros.html` Jinja2 macro (`pagination_nav`) is used consistently across all paginated admin views, including Sessions. Logs `_MAX_LINES` raised from 500 to 2000 to support deeper history browsing.

---

## [0.4.17] — 2026-05-27

### Added

- **`hunt.support.crypt`** — new module exposing `encrypt(value)` and `decrypt(token)`, Fernet-based symmetric encryption keyed to `APP_KEY`. Intended for any service that needs to store sensitive strings at rest. `TwoFactor.encrypt_secret` / `decrypt_secret` now delegate to it.

### Security

- **TOTP replay protection** — `TwoFactor.mark_used(user_id, code)` / `is_used(user_id, code)` now prevent a verified TOTP code from being accepted a second time within the 90-second validity window. The challenge and setup-confirm controllers both check and record used codes. State is stored in Redis (key `hunt:2fa:used:{user_id}:{code}`, 90 s TTL) with a thread-safe in-memory fallback for installs without Redis.

---

## [0.4.15] — 2026-05-26

### Added

- **Session registry** (`hunt.session.registry`) — a `user_sessions` database table now tracks active authenticated sessions (session ID → user ID + guard + IP + user agent + last login time). Sessions are registered on `Auth.login()` / `Auth.attempt()` and deregistered on `Auth.logout()`. The table is opt-in: run `hunt session:table` then `hunt migrate` to create it; all registry writes are silently no-oped if the table does not exist.
- **`hunt session:table`** — new CLI command that generates the `user_sessions` migration (mirrors `hunt queue:table`).
- **Session revocation** — `revoke_sessions_for(user_id, guard=None)` (importable from `hunt.session`) and `SessionRegistry().revoke_for_user(user_id)` perform a targeted O(k) revocation: fetch session IDs from the registry, delete the corresponding session data (file or Redis), then remove the registry rows. No full-scan needed.
- **`Password.reset(revoke_sessions=True)`** — the password broker's `reset()` method accepts an optional `revoke_sessions` keyword argument. When `True`, all active sessions for the user are revoked immediately after the password is changed.
- **Admin sessions panel** — new `/hunt-admin/sessions` page in the System nav group. Admins can see all active sessions (user ID, guard, IP address, user agent, last login time) and revoke individual sessions or all sessions for a specific user with a single click.
- **`ColumnDef.primary_key()`** — new fluent method on the Blueprint column definition to mark a non-integer column as a `PRIMARY KEY` (used by the `user_sessions` migration for its `VARCHAR(64)` PK).

---

## [0.4.14] — 2026-05-26

### Added

- **`BelongsToMany` admin field** — new `BelongsToMany(name, RelatedResource, relation_method)` field for many-to-many relationships. Renders a searchable chip/tag multi-select widget on create/edit forms and displays linked chips on the detail view. Pivot table is synced automatically on save (detach-all then re-attach).
- **HasMany panels on edit page** — the edit page now renders read-only relationship tables for `HasMany` fields below the form, consistent with the detail (show) view.

### Fixed

- **`DataError: invalid input syntax for type integer: ""`** — optional `BelongsTo` FK fields that were left blank submitted an empty string to the database. `_collect_data` now converts empty-string values for `BelongsTo` attributes to `None` so the column receives `NULL`.
- **`TypeError: can't access property "indexOf", n[i].href is null`** — the EasyMDE Font Awesome detection patch from 0.4.12 introduced a null-dereference. The original `n[i].href&&…` null guard only covered the first `indexOf`; due to operator precedence the appended `||n[i].href.indexOf(…)` evaluated outside the guard when a stylesheet with a null `href` was present (e.g. `<style>` elements injected via JS). Fixed by wrapping both `indexOf` calls in parentheses so the null guard applies to both: `n[i].href&&(…||…)`.

---

## [0.4.12] — 2026-05-26

### Fixed

- **EasyMDE Font Awesome CDN injection** — EasyMDE's JS checks for Font Awesome by looking for the maxcdn CDN href; since hunt serves FA locally under a different path, the check failed and the browser attempted to load the CDN stylesheet, violating strict CSPs. Patched `easymde.min.js` (and updated `scripts/build_admin_assets.py`) to also accept any `href` containing `"font-awesome"`, so the locally-bundled file satisfies the check.
- **Admin `img-src` CSP blocking storage images** — `Admin._render()` now sets an explicit CSP on every admin response with `img-src * data: blob:`, allowing images served from any storage backend (local, S3, CDN). `SecureHeaders` middleware was updated to skip setting the CSP header when the response already carries one, so the admin CSP is not overwritten by the global policy.

---

## [0.4.11] — 2026-05-26

### Fixed

- **Media manager CSRF 419** — upload and delete `fetch` calls in both the modal and the standalone media page were missing the CSRF token. Added `_token` to `FormData` for uploads and `X-CSRF-TOKEN` header for delete requests. The token is now exposed via `window._huntAdmin.csrfToken` in the layout.

---

## [0.4.10] — 2026-05-26

### Fixed

- **`DeprecationWarning` in JSON log formatter** — replaced `datetime.utcfromtimestamp()` with `datetime.fromtimestamp(..., datetime.UTC)` in `_JsonFormatter`. The deprecated form is scheduled for removal in Python 3.15.

---

## [0.4.9] — 2026-05-26

### Added

- **Python 3.14 support** — added `Programming Language :: Python :: 3.14` classifier.

---

## [0.4.8] — 2026-05-26

### Added

- **Admin media manager** — a global media manager at `<prefix>/media` for uploading, browsing, and deleting images. Supports JPEG, PNG, GIF, WebP, and SVG. Files are stored on a configurable storage disk (`Admin.media_disk`, default `"public"`, path `Admin.media_path`, default `"media"`). The page supports drag-and-drop upload, image grid view, URL copy on click, and per-file deletion.
- **`MediaBrowser` field** — a new admin field type (`field_type = "media_browser"`) that renders a "Browse Media" button on create/edit forms. Clicking it opens the media manager modal and populates a read-only URL input that can be copied to the clipboard. The field does not save any value to the database.
- **EasyMDE media toolbar button** — the Markdown field's EasyMDE editor now includes a media manager toolbar button (image icon) that opens the media manager modal and inserts the selected image as `![name](url)` at the cursor position.
- **`/media/api` JSON endpoint** — `GET <prefix>/media/api` returns a JSON list of uploaded media files with `name`, `path`, `url`, and `size` fields.
- **23 unit tests** for the media controller and `MediaBrowser` field, covering API listing, upload validation (MIME type guard), path traversal prevention on delete, and field visibility defaults.

---

## [0.4.7] — 2026-05-26

### Fixed

- **`tojson` filter HTML-escaped inside `<script>` tags** — the custom `tojson` filter returned a plain `str`, which Jinja2's auto-escaping converted to `&quot;` inside script blocks. This caused a `SyntaxError` in the browser and left `window._huntAdmin` undefined. Fixed by wrapping the output in `markupsafe.Markup` so Jinja2 treats it as already-safe content.

---

## [0.4.6] — 2026-05-26

### Fixed

- **Admin asset route not matching** — `{filename:path}` was interpreted as a regex constraint (matching only the literal string `"path"`). Changed to `{filename:.+}` so all asset filenames, including font subdirectory paths like `fonts/fontawesome-webfont.woff2`, resolve correctly.
- **`{{ prefix }}` and `{{ resource_key }}` empty in form macros** — Jinja2 `{% from … import %}` macros do not inherit the calling template's context. `prefix` and `resource_key` were silently rendering as empty strings in `_form.html`, breaking EasyMDE asset URLs and the BelongsTo searchable autocomplete. Fixed by injecting `window._huntAdmin = {prefix, resourceKey}` into the layout once and reading from that global in all macro JavaScript.

---

## [0.4.5] — 2026-05-26

### Added

- **Bundled admin static assets** — all external CDN dependencies for the admin panel are now vendored and served locally from `<prefix>/assets/`. No outbound requests to `cdn.tailwindcss.com`, `unpkg.com`, or `maxcdn.bootstrapcdn.com` are made at runtime. Assets: Tailwind CSS (built from templates via CLI), Trix (CSS + JS), EasyMDE (CSS + JS), Font Awesome 4.7 (CSS + fonts). The EasyMDE CSS is patched at build time to remove its external Font Awesome `@import`, eliminating the CSP violation.
- **Admin asset build pipeline** — `Makefile` with `make assets` (downloads/patches vendor files via `scripts/build_admin_assets.py`) and `make css` (runs Tailwind CLI). `package.json` declares `tailwindcss` and `@tailwindcss/typography` as dev dependencies. `tailwind.config.js` scans all admin templates so only used utility classes are emitted.
- **Static asset serving controller** — `hunt.admin.controllers.static_assets` serves files from `src/hunt/admin/static/` at `<prefix>/assets/{filename}` outside the `AdminGate` middleware (no auth required for CSS/JS). Path traversal is blocked via an allowlist of extensions and `Path.resolve()` containment check.

---

## [0.4.4] — 2026-05-26

### Added

- **`Markdown` admin field** — new `Markdown` field type for admin resources. Renders an [EasyMDE](https://github.com/Ionaru/easy-markdown-editor) editor (loaded lazily from CDN) with bold, italic, headings, tables, images, and links in the toolbar. On the detail/show page, markdown is converted to sanitized HTML via Python-Markdown (`tables`, `fenced_code`, `nl2br` extensions) and `nh3`. Added `markdown>=3.4,<4.0` as a framework dependency.

---

## [0.4.3] — 2026-05-26

### Added

- **Admin `BelongsTo` links on detail page** — `BelongsTo` field values on the resource detail (show) page are now rendered as clickable links to the related resource. Eager-loading of BelongsTo relations is also applied on the detail page so the linked label is resolved rather than the raw FK integer.
- **`hunt context`** — new command that emits a compact snapshot of the current project (routes, models with fillable/relations, middleware, env keys, directory structure) in markdown or `--json`. Designed for AI coding assistants; uses AST-based model discovery so no database connection is required. `--no-routes` skips the app boot entirely for fast offline use.
- **`hunt make:model` expanded flags** — `-f/--factory`, `-s/--seeder`, `-p/--policy` flags added alongside the existing `-m` and `-c`. `--all` is a shorthand for all five. `--fields "col:type …"` now accepted directly on `make:model`, populating `fillable` in the model stub and generating typed blueprint columns in the migration (previously only available via `make:crud`).
- **`hunt make:test`** — new scaffold command. `--unit` (default) writes to `tests/unit/`, `--feature` writes to `tests/feature/`. Both stubs include correct pytest class structure and imports.
- **`--dry-run` and `--json` flags on all core `make:*` commands** — every `make:model`, `make:controller`, `make:migration`, `make:factory`, `make:seeder`, `make:policy`, `make:crud`, and `make:test` now accepts `--dry-run` (prints what would be written without touching the filesystem) and `--json` (emits a machine-readable `{"created": […], "dry_run": […], "skipped": […]}` summary). Backed by a shared `_output.py` singleton so all make helpers inherit the mode automatically.
- **`llms.txt` and `llms-full.txt`** — added to the hunt-docs `public/` directory. `llms.txt` is an index conforming to the llms.txt spec with links to every doc section. `llms-full.txt` is a dense, self-contained ~3000-token code-heavy reference covering routing, controllers, models, query builder, relations, migrations, validation, views, auth, caching, sessions, queues, events, mail, and all CLI commands — optimised for AI agents that need full framework context in a single fetch.
- **CLI docs updated** — `docs/cli/page.md` now documents `hunt context`, the expanded `make:model` flags, `make:test`, and the universal `--dry-run`/`--json` flags on all make commands.
- **AI Agents docs page** — new `docs/ai-agents/` page covering `hunt context`, `llms.txt`/`llms-full.txt`, dry-run/JSON scaffolding workflow, and recommended agent session patterns.
- **Admin Metrics docs rewritten** — corrected to reflect the actual constructor-based API (`ValueMetric(name, resolver, ...)`) instead of the incorrect subclass pattern previously documented.
- **Docs nav restructured** — renamed "Digging Deeper" → "Backend Services"; moved Validation into The Basics; reordered Security to Authentication-first; added Observability group (Logging, Testing) and Internationalization group (Localization); AI Agents section added before CLI Reference.

### Fixed

- **`RichText` sanitizer crash** — `nh3.clean` raised `ValueError` when `"rel"` was listed in allowed `<a>` attributes while `link_rel` was also set. Removed the redundant `"rel"` entry; `link_rel` already injects `noopener noreferrer nofollow` on every link.
- **`make:crud --dry-run` idempotency** — the route-append step now uses a quoted-sentinel check (`"/<prefix>"`) instead of a bare substring match, preventing false positive skips and correctly respecting `--dry-run` mode.

---

## [0.3.9] — 2026-05-25

### Added

- **Test suite expansion** — 174 new unit tests covering previously untested modules: log viewer controller (`_parse_line`, `_tail`, `_log_path`, `index`), health controller (`_storage_size`, `_check_database`, `_check_cache`, `_check_queue`), cache inspector controller (`_list_array`, `_list_file`, `_truncate`, `delete`, `flush`), schedule admin controller (`_next_run`, `_load_scheduler`), route explorer controller (`_middleware_name`, `index`), `ArrayStore` and `FileStore` cache backends (all operations: put/get/forget/flush/add/pull/increment/decrement/get_many/put_many/remember), log manager (`_JsonFormatter`, `_build_channel` all drivers, `_LogManager` multi-channel configuration), and `ThrottleRequests` sliding-window rate limiter (rate limiting, window expiry, per-IP/path isolation, response headers, 429 handling).

### Fixed (security)

- **CRLF injection in cookie headers** (`http/response.py`) — `with_cookie()` now strips `\r` and `\n` from name and value before interpolation, preventing HTTP response splitting.
- **Open redirect via backslash** (`http/response.py`) — `_is_safe_redirect()` now rejects URLs beginning with `/\` or `\/`, which some browsers normalize to `//host`.
- **TOTP secret stored plaintext in session** (`auth/controllers/two_factor.py`) — The pending secret is now encrypted with `TwoFactor.encrypt_secret()` before being stored and decrypted in `confirm()`, so a session leak does not expose the raw TOTP seed.
- **TOTP replay attack** (`auth/controllers/two_factor.py`) — The last accepted TOTP code is tracked in the session (`_2fa_last_totp`); a re-submitted identical code is rejected for the full 30-second window.
- **2FA brute-force lockout** (`auth/controllers/two_factor.py`) — After 5 consecutive failed challenge attempts the pending session is cleared and the user is redirected to `/login`, preventing online guessing.
- **SQL injection via limit/offset** (`database/query_builder.py`) — `limit()` and `offset()` now cast their argument to `int`, raising `ValueError` on non-numeric input rather than interpolating a raw string into the query.
- **Timing side-channel in password reset** (`auth/passwords.py`) — `send_reset_link()` now always executes `DELETE` against the reset table before checking whether the email exists, equalling the DB round-trip count for both known and unknown addresses.
- **Session GC blocking the event loop** (`session/store.py`) — Probabilistic GC (1 % of requests) is now offloaded to the default thread-pool executor via `run_in_executor`, preventing synchronous directory scans from stalling async request handling.
- **External CDN script in debug panel** (`http/middleware/debug_panel.py`) — Removed the `<script>` that injected `cdn.tailwindcss.com` when `APP_DEBUG=true`; the panel relied solely on inline styles so no functionality is lost.
- **Hardcoded Mailpit dashboard port** (`console/commands/lodge_install.py`) — The compose binding for the Mailpit UI is now `${MAILPIT_DASHBOARD_PORT:-8025}:8025`, and `MAILPIT_DASHBOARD_PORT=8025` is written to `.env` on install so the port is overridable without editing the generated file.

---

## [0.3.7] — 2026-05-25

### Added

- **Lodge** — Docker Compose development environment tool inspired by Laravel Sail. `hunt lodge:install` scaffolds a complete local dev environment: a dev-optimised `docker/Dockerfile` (non-root user, build tools, layer-cached pip install), a `compose.yaml` for the app service, and a `lodge` bash wrapper script that abstracts `docker compose` with hunt-specific ergonomics. Optional services selectable with `--with`: `pgsql` (Postgres 16), `mysql` (MySQL 8), `redis` (Redis alpine), `mailpit` (email testing UI on port 8025), `minio` (S3-compatible object storage with console on port 9001). Each selected service is wired into `depends_on` with healthchecks, and the corresponding `.env` keys are updated automatically. `hunt lodge:add <service>` adds a service to an existing Lodge setup without regenerating from scratch. The `lodge` script supports: `up`, `down`, `stop`, `restart`, `build`, `shell`, `python`, `hunt <cmd>`, `test`, `pip`, `logs`, `share` (via cloudflared), and pass-through to `docker compose` for anything else. TTY is detected automatically so `exec` flags work correctly in both interactive terminals and CI pipelines.

---

## [0.3.6] — 2026-05-25

### Added

- **Health page** (`admin/controllers/health.py`, `admin/templates/admin/health/index.html`): live connectivity checks for the database (SELECT 1 with latency), cache (put/get/forget probe), and queue (pending job count). Shows overall Healthy/Degraded badge, plus system info panel (hunt version, Python version, platform, APP_ENV, debug mode, storage/log disk usage). Accessible at `/hunt-admin/health`.
- **Cache inspector** (`admin/controllers/cache_inspector.py`, `admin/templates/admin/cache/index.html`): browse all keys in the active cache store with their TTL and truncated value. Supports Redis (SCAN over prefixed keys), file store (directory walk), and array store (in-memory dict). Individual keys can be deleted; a Flush All button clears the entire store. Accessible at `/hunt-admin/cache`.
- **Schedule monitor** (`admin/controllers/schedule.py`, `admin/templates/admin/schedule/index.html`): loads `app/console/schedule.py` at request time and displays all registered tasks with their cron expression, description, next run timestamp (scanned forward up to 8 days), and flags (no-overlap, background, environment constraints). Due-now tasks are highlighted. Accessible at `/hunt-admin/schedule`.
- **Route explorer** (`admin/controllers/route_explorer.py`, `admin/templates/admin/routes/index.html`): lists every registered route with method badge(s), URI, named route key, and middleware chain. Supports filtering by HTTP method and free-text search across URI/name. The router reference is captured when `Admin.register_to(router)` is called. Accessible at `/hunt-admin/routes`.
- All four pages added to the System group in the admin sidebar navigation.

---

## [0.3.5] — 2026-05-25

### Added

- **Log viewer in Hunt Admin** (`admin/controllers/logs.py`, `admin/templates/admin/logs/index.html`): a read-only "Logs" page under the System group in the admin sidebar. Reads the last 500 lines of the application log file (default `storage/logs/hunt.log`, overridable via `LOG_PATH`), parses both JSON and text log formats, and renders a filterable table with level badges, timestamps, messages, and request IDs. Exception tracebacks embedded in JSON log entries are expandable inline. Supports filtering by level (debug/info/warning/error/critical) and free-text search across message and request ID.

---

## [0.3.4] — 2026-05-25

### Fixed

- **Route model binding returns 404 on missing record** (`http/kernel.py`): `resolve_route_binding` raises `ValueError` when a record is not found. The kernel now catches `ValueError` at the binding call site and converts it to `HttpException(404)`, so missing route model parameters produce a 404 response instead of an unhandled 500.
- **`_fernet_key()` raises `RuntimeError` when `APP_KEY` is unset** (`auth/two_factor.py`): previously, an empty `APP_KEY` silently produced a deterministic constant Fernet key, making all stored TOTP secrets trivially decryptable. A missing key now raises immediately with an actionable error message.
- **`APP_KEY` rotation no longer traps 2FA users in a redirect loop** (`auth/controllers/two_factor.py`): a `decrypt_secret` failure (e.g. after key rotation or data corruption) now clears the pending 2FA session and redirects to `/login` with an informative message, instead of looping on `/two-factor/challenge` indefinitely.
- **Recovery code bcrypt scan skipped for TOTP-shaped input** (`auth/controllers/two_factor.py`): a failed TOTP check previously triggered a serial `bcrypt.checkpw` scan of all 8 recovery code hashes (~800 ms CPU per attempt). Recovery codes always contain a hyphen; a format guard now skips the scan entirely for 6-digit TOTP codes.
- **Cache hit path now calls `_eager_load`** (`database/query_builder.py`): `get()` with `remember()` and `.with_()` returned models with no eager-loaded relations on cache hits. Relations are now loaded on both cache-hit and cache-miss paths.
- **`pluck()` no longer overwrites scope-applied `_selects`** (`database/query_builder.py`): `pluck()` was directly mutating `mat._selects` after `_materialize()`, silently discarding any SELECT modifications applied by global scopes. It now uses `select_raw()` which returns a proper clone.
- **`ModelMeta` only calls `boot()` on the class that defines it** (`database/model.py`): `boot()` was called for every subclass including those that inherit it from a parent, causing the parent's `boot()` body to re-run with `cls` pointing to the subclass. Boot is now only triggered when the class explicitly defines `boot` in its own namespace.

---

## [0.3.3] — 2026-05-25

### Fixed

- **`_url` validation rule now validates the host** (`validation/rules.py`): the rule previously accepted strings like `http://` (no host). It now uses `urllib.parse.urlparse` and requires a non-empty `netloc`.
- **Query cache key now includes eager-load relations** (`database/query_builder.py`): `remember()` previously shared a cache entry between queries with and without `.with_()` relations, causing eager-loaded relations to be missing on cache hits. The key now includes the sorted set of relation names.
- **HTML injection in mail rendering fixed** (`mail/message.py`): text passed to `.greeting()`, `.line()`, `.outro()`, and `.action()` is now HTML-escaped before being inserted into the email template.
- **Duplicate named routes now raise `ValueError`** (`http/router.py`): registering two routes with the same name silently overwrote the first. A `ValueError` is now raised immediately at registration time.
- **`request.query()` always returns a single string** (`http/request.py`): previously returned a `list` when a query parameter appeared multiple times, making the return type inconsistent. It now always returns the first value. A new `query_all(key)` method returns all values as a list for multi-value params.

### Changed

- **`resource()` routes PUT and PATCH together** (`http/router.py`): the separate PUT and PATCH routes are now registered as a single `match(["PUT", "PATCH"], ...)` route named `{prefix}.update`, following REST convention and eliminating the unnamed PATCH route.
- **Cache `remember()` uses atomic `add()`** (`cache/manager.py`): all three stores (`RedisStore`, `ArrayStore`, `FileStore`) now write via `add()` instead of `put()` after computing the value, so a concurrent writer that already stored the result wins rather than both writes racing.
- **Sync queue driver enforces job timeouts** (`queue/drivers/sync.py`): jobs with a `timeout` attribute are now run under `SIGALRM` on POSIX systems. A `JobTimeoutError` is raised if the job exceeds its limit.

---

## [0.3.2] — 2026-05-25

### Security

- **2FA recovery codes now hashed at rest** (`auth/two_factor.py`, `auth/controllers/two_factor.py`): recovery codes are hashed with bcrypt before being stored in the database. Verification uses constant-time `bcrypt.checkpw` instead of a plain string comparison, and the matched hash is removed on use. The plaintext codes are only ever visible immediately after generation.
- **TOTP secret encrypted at rest** (`auth/two_factor.py`, `auth/controllers/two_factor.py`): the TOTP secret is now encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before storage, keyed to `APP_KEY`. The secret is decrypted only in memory at verification time.
- **`manage` view no longer exposes stored codes** (`views/auth/two_factor/manage.html`): the manage page now shows only the count of remaining recovery codes. Plaintext codes are shown once on the `recovery` view immediately after generation or regeneration.
- **Debug panel no longer exposes auth session keys** (`http/middleware/debug_panel.py`): `_auth_id`, `_2fa_pending`, and `_2fa_pending_secret` are now in `_PRIVATE_SESSION_KEYS` and are filtered from the session tab even when `APP_DEBUG=true`.
- **HSTS enabled by default** (`http/middleware/secure_headers.py`): `Strict-Transport-Security` now defaults to `max-age=31536000` (1 year). Set `SECURE_HSTS_SECONDS=0` to disable for local development.
- **Default Content-Security-Policy** (`http/middleware/secure_headers.py`): CSP now defaults to `default-src 'self'` instead of being omitted entirely. Override with `SECURE_CONTENT_SECURITY_POLICY`.
- **Password reset tokens hashed with bcrypt** (`auth/passwords.py`): reset tokens are now hashed with bcrypt before storage instead of HMAC-SHA256, making offline brute-force infeasible even if both the DB and `APP_KEY` are leaked.
- **Password reset timing hardened** (`auth/passwords.py`): token generation and hashing now always run before the user lookup, eliminating the timing difference that previously allowed enumeration of registered email addresses.
- Added `cryptography>=41.0,<45.0` as an explicit dependency.

---

## [0.3.1] — 2026-05-24

### Added

- **Global model scopes** (`model.py`, `query_builder.py`): models can now register always-on query constraints via `boot()` + `add_global_scope(name, fn)`. The scope function receives the `QueryBuilder` and returns a modified one. Scopes are applied lazily at execution time via `_materialize()`, so callers can opt out with `Model.without_global_scope("name")` or `Model.without_global_scopes()` before the query runs. Scopes propagate through all terminal methods (`get`, `first`, `count`, `min`, `max`, `avg`, `sum`, `pluck`, `update`, `delete`).
- **`route_key_name` for route model binding** (`model.py`, `http/kernel.py`): models can set `route_key_name: ClassVar[str] = "slug"` to bind routes on a column other than `id`. The kernel now calls `Model.resolve_route_binding(value)` instead of `find_or_fail`, which respects `route_key_name` and raises `ValueError` (→ 404) when no record is found.
- **Query result caching — `.remember(ttl, key=None)`** (`query_builder.py`): chain `.remember(300)` on any query to cache the raw rows for `ttl` seconds using the active Cache driver. The cache key is derived from an MD5 of the SQL and bindings, or from the explicit `key` argument. A cache hit re-hydrates the cached rows into model instances without hitting the database.
- **`route:export` — OpenAPI 3.1 spec generation** (`route_export.py`, `console/kernel.py`): `hunt route:export` outputs a JSON OpenAPI 3.1 spec built from the registered route table. `--output path/to/openapi.json` writes to a file; `--title` and `--version` set `info.title` / `info.version`. Path parameters are extracted from `{param}` URI segments; action docstrings supply `summary` and `description`; POST/PUT/PATCH routes get a generic `application/json` request body schema.
- **`env:check` pre-serve gate** (`env_check.py`, `serve.py`, `serve_production.py`): `hunt serve` now prints a warning when required environment variables (`APP_KEY`, `DATABASE_URL`) are missing so developers notice early. `hunt serve:production` hard-aborts with exit code 1 on the same condition — a production server must not start misconfigured. The shared `check_required()` helper in `env_check.py` loads `.env` before checking.

---

## [0.3.0] — 2026-05-23

### Added

- **`vendor:publish` — full view overriding** (`vendor_publish.py`): users can now publish all framework views to `resources/views/` for customisation. `vendor:publish` (no tag) or `--tag views` copies every framework view; `--tag views:auth` copies only auth views; `--tag views:components` (or the existing `--tag components`) copies only UI components. Any file placed in `resources/views/` automatically takes priority over the framework's built-in copy (the `ChoiceLoader` order in `ViewFactory` already ensures this). If a destination file already exists (i.e. the user has already customised it), the framework's copy is placed under `resources/views/framework/` instead so the original is never overwritten and the reference template remains accessible.

### Fixed

- **`TrustProxies` — `trust_all` returned wrong IP** (`trust_proxies.py`): when `TRUSTED_PROXIES=*`, the loop broke on the first (rightmost) IP in `X-Forwarded-For`, returning the last proxy's address instead of the real client IP. The loop is now skipped entirely when `trust_all` is set and `ips[0]` (the leftmost, real client IP) is used directly.
- **Uploaded filename path traversal** (`request.py`): `UploadedFile.filename` could contain `../` sequences, which would surprise code that consumed the filename directly before calling `store()`. The filename is now reduced to its basename via `os.path.basename()` at parse time, so the stored value is always a plain filename with no directory components. The storage layer's own root-confinement check remains as a second line of defence.

---

## [0.2.44] — 2026-05-23

### Added

**M34 — TrustProxies middleware**
- **`TrustProxies`** middleware (`hunt.http.middleware.trust_proxies`) — when a request arrives from a configured trusted proxy, rewrites `request.ip` from `X-Forwarded-For` and `request.scheme` from `X-Forwarded-Proto`. Without this, all requests behind nginx/ALB appear to come from the proxy IP, breaking rate limiting, session `Secure` detection, and IP logging. Configure via `TRUSTED_PROXIES` env var (comma-separated IPs/CIDRs) or subclass `proxies` class attribute. Set `TRUSTED_PROXIES=*` to trust all (development only).

**M35 — Configurable body size limit**
- `MAX_BODY_SIZE` env var overrides the default 10 MB limit on request bodies. Set to bytes (e.g. `MAX_BODY_SIZE=52428800` for 50 MB). Requests exceeding the limit still return 413 immediately. The `.env.example` scaffold includes a commented-out example.

**M36 — Docker scaffold**
- `hunt new` now writes a `Dockerfile` and `.dockerignore` to every new project. The Dockerfile uses `python:3.12-slim`, installs dependencies, and runs `hunt serve:production` on port 8000.

**M37 — Maintenance mode**
- **`hunt down [--message "..."] [--retry 60]`** — writes a `.maintenance` sentinel file and puts the app into maintenance mode. All visitors receive a 503 with `Retry-After` header.
- **`hunt up`** — removes `.maintenance` and restores normal service.
- **`MaintenanceMode`** middleware (`hunt.http.middleware.maintenance`) — checks for the sentinel file on every request. Subclass and set `bypass_ips` to allow specific IPs through during maintenance (e.g. your own IP for smoke-testing before going live).

**M38 — `migrate:status --pending` exit code**
- `hunt migrate:status --pending` exits with code 1 if any migrations have not been run. Use in CI/CD pipelines to gate deployments: `hunt migrate:status --pending && hunt serve:production`.

---

## [0.2.43] — 2026-05-22

### Added

**M30 — Security headers middleware**
- **`SecureHeaders`** middleware (`hunt.http.middleware.secure_headers`) — adds `X-Frame-Options`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy` to every response. Opt-in `Strict-Transport-Security` (set `SECURE_HSTS_SECONDS` > 0) and `Content-Security-Policy` (set `SECURE_CONTENT_SECURITY_POLICY`). All values configurable via env vars or subclassing.

**M31 — Lifespan hooks**
- **`Application.on_startup(callable)`** — registers a callback (sync or async) run when the ASGI server starts up; ideal for warming DB connections or pre-loading data.
- **`Application.on_shutdown(callable)`** — registers a callback run on graceful shutdown; use for connection teardown, flushing buffers, etc.
- **`HttpKernel(app=...)`** — new optional `app` parameter wires the kernel to the `Application` so lifespan hooks are dispatched. The `new` project scaffold now passes `app=application` automatically. Hook errors are caught and logged without crashing the server.

**M32 — `hunt env:check`**
- New CLI command that validates environment configuration before deploying. Checks required vars (`APP_KEY`, `DATABASE_URL`), warns on suboptimal settings (`APP_DEBUG=true`, `SESSION_SECURE=false`, etc.), and lists optional integrations. Exits with code 1 if any required var is missing.

**M33 — Redis-backed rate limiting**
- **`RedisThrottleRequests`** middleware (`hunt.http.middleware.throttle`) — async sliding-window rate limiter backed by Redis sorted sets. Safe for multi-worker Uvicorn/Gunicorn deployments because counters are shared across all processes. Requires `pip install redis`. Configure via `REDIS_URL` or `REDIS_HOST`/`REDIS_PORT`/`REDIS_DB`/`REDIS_PASSWORD`. Subclass and set `max_attempts`/`decay_seconds` to tune limits per route group.

---

## [0.2.42] — 2026-05-22

### Fixed

- **2FA views used non-existent Jinja2 globals** — All five built-in 2FA views (`setup`, `confirm`, `recovery`, `challenge`, `manage`) were calling `csrf_field()`, `session_has()`, and `session_get()` which are not registered in the Jinja2 environment. Views now use `{{ csrf_token }}` (injected by `ViewFactory._inject_csrf`) and `flash.get("key")` (injected by `ViewFactory._inject_flash`) consistent with all other framework auth views.
- **2FA views used `| raw` filter** — Jinja2 has no `raw` filter; the correct filter is `safe`. Replaced by using the `{{ csrf_token }}` variable directly, eliminating the filter entirely.
- **2FA disable form used `DELETE` method override** — HTML forms only support GET/POST; the disable form was submitting `POST /two-factor` with `_method=DELETE` but the framework has no method-override middleware. Added `POST /two-factor/destroy` route to the scaffold routes stub and updated the manage view to use it.
- **`make:2fa-controllers` published stale templates** — The command now copies templates directly from the package's `src/hunt/views/auth/two_factor/` directory instead of maintaining a separate inline copy, so published templates are always in sync with the built-in views.
- **2FA views extended wrong layout** — Views were extending `layouts/app.html` (a user-provided Tailwind layout) instead of the built-in `auth.layout`. Rewritten to use `@extends('auth.layout')` and the framework's auth CSS classes (`alert-error`, `alert-success`, `btn-submit`, `field`) matching all other built-in auth views.

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

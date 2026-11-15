# Upgrade guide

This guide is also published at [hunt-framework.com/docs/upgrading](https://hunt-framework.com/docs/upgrading), together with the notes for earlier versions.

## Upgrading from 0.4 to 0.5

0.5 changes default behaviour in the ORM, validation, queue, scheduler, sessions, HTTP kernel, mail, cache and admin panel. Most applications need a few small edits. Allow an hour for a typical app, and run your test suite at the end.

Work through the sections in order: **Steps** is what everyone does, **Changes you must check** lists the changes that break working code, and **Other behaviour changes** is the complete reference by area.

### Steps

1. **Update the dependency.** In your app's `pyproject.toml`, depend on `hunt-framework>=0.5,<0.6`, then reinstall.

   ```bash
   uv pip install -e .        # or: pip install -e .
   ```

   If your app was created by an older `hunt new`, its dependency is spelled `hunt`. That is a different package on PyPI. `hunt upgrade` (step 3) fixes it for you.

2. **Install the extras you use.** Optional integrations are no longer installed by default.

   | You use | Install |
   |---|---|
   | PostgreSQL | `hunt-framework[postgres]` |
   | Redis (cache, sessions, queue, rate limiting) | `hunt-framework[redis]` |
   | S3 storage | `hunt-framework[s3]` |
   | Sentry | `hunt-framework[sentry]` |
   | `hunt tinker` with IPython | `hunt-framework[tinker]` |

   `ipython`, `asyncpg`, `aiomysql`, `aiosqlite` and `pydantic` are no longer installed with the framework. If your own code imports one of them, add it to your app's dependencies.

3. **Run `hunt upgrade`.** Preview first, then apply.

   ```bash
   hunt upgrade --dry-run
   hunt upgrade
   ```

   It adds new scaffold files, fixes the `hunt` dependency name, offers to add the `MaintenanceMode` middleware to `bootstrap/app.py` (without it `hunt down` does nothing), and lists models that need a `fillable` declaration (see below). Add `--with-security-headers` if you also want the `SecureHeaders` middleware; check your pages afterwards, because it sends a Content-Security-Policy.

4. **Declare `fillable` on your models.** See [Models are guarded by default](#models-are-guarded-by-default).

5. **Create the new queue table** if you use the database queue driver. (Apps created by `hunt new` 0.5 already have this migration.)

   ```bash
   hunt queue:table --processed-only
   hunt migrate
   ```

6. **Deploy, then re-queue old jobs.** Jobs that were queued by 0.4 are moved to the failed-jobs store when a 0.5 worker picks them up. Bring them forward with:

   ```bash
   hunt queue:retry --all
   ```

   Drain your queues before deploying if you can; then there is nothing to retry.

7. **Convert stored sessions** (optional). Sessions keep working and convert themselves as they are used. To convert the rest immediately:

   ```bash
   hunt session:hash-ids
   ```

8. **Check your environment.**

   - The SMTP mailer's `encryption` setting must be `tls`, `ssl` or `none`. If your `config/mail.py` passes `MAIL_ENCRYPTION` straight through, an empty value now raises; set it explicitly (`none` for a local mail catcher).
   - If you serve production over plain HTTP and your `config/session.py` leaves `secure` unset, set `SESSION_SECURE=false`.
   - If you run `hunt config:cache`, re-run it after every config or `.env` change. It is now actually loaded.
   - Review your scheduled tasks: weekday numbers changed (see below).

9. **If you published the admin templates,** re-publish them with `hunt admin:publish --force`. Until you do, set `Admin.csp_allow_inline_scripts = True`.

10. **Run your tests.**

Rolling back to 0.4 after deploying 0.5 logs every user out, because 0.4 cannot find sessions stored by 0.5.

### Changes you must check

#### Models are guarded by default

A model with neither `fillable` nor `guarded` used to accept every column in `create()` and `fill()`. It now raises `MassAssignmentError`.

```python
class Post(Model):
    fillable = ["title", "body"]      # only these can be mass-assigned

class Setting(Model):
    guarded = []                      # opt out: everything is assignable
```

- The primary key is mass-assignable only when `fillable` lists it. Do that for models whose key your application supplies (a code or UUID): `fillable = ["code", "name"]`.
- Set privileged columns explicitly: `user.is_admin = True`, or `user.force_fill({...})`, or `user.update(is_admin=True)`.
- Factories are unaffected.

#### Validation returns only validated fields

`validate()` and `validated()` used to return the whole input. They now return only the fields your rules name. If you passed the result to `create()`, add a rule for every field you want to keep.

Other validation changes that can break existing rules:

- An unknown rule name, a bad regex or a bad rule parameter raises `ValueError` when the validator is built. Typos in rule strings that were silently ignored now fail loudly.
- In the string form, everything after `regex:` belongs to the pattern. Use the list form to put more rules after a regex: `["regex:^[a-z|]+$", "max:20"]`.
- `integer|max:100` on form input now compares the number, not the string length. The same applies to `min`, `size` and `between`.
- `required` rejects blank strings and empty lists.
- `gt:0`, `lt:10`, `before:2030-01-01` treat a number or date as a literal. They only refer to another field when the parameter is not a number or date.
- File rules (`image`, `mimes`, `max_file_size`) now receive uploaded files. `required|image` used to fail for every upload and now works.

#### Scheduler weekdays

Day-of-week now follows standard cron: `0` and `7` are Sunday, `1` is Monday.

| Expression | 0.4 | 0.5 |
|---|---|---|
| `weekly()` | Monday 00:00 | Sunday 00:00 |
| `0 9 * * 1-5` | Tuesday to Saturday | Monday to Friday |
| `weekly_on(n)` | shifted by one day | standard |

When both day-of-month and day-of-week are restricted, a task runs when either matches (standard cron). An invalid expression raises when the task is registered.

#### Queue

- **Payload format changed.** See step 6.
- **Jobs must serialize.** The `sync` driver now runs a rebuilt copy of the job after a JSON round trip, the same as the real drivers. A job attribute that is not JSON-safe raises `JobSerializationError` at dispatch. Models, `datetime`, `date` and `Decimal` are supported. A required `__init__` argument must be stored under a public attribute of the same name.
- **Queued mailables and notifications** must be module-level classes. A queued notification needs a saved model as its notifiable.
- **Stale jobs.** A job still undelivered 7 days after it became due is failed as stale. Change the window with `QUEUE_PROCESSED_RETENTION` (seconds) or `processed_retention` in `config/queue.py`.
- **`retry_after`** (default 90 seconds, `QUEUE_RETRY_AFTER`) is how long a job may stay reserved before another worker takes it over. It must be longer than your longest job.
- **Redis driver** uses new keys: `<prefix>:<queue>:processing`, `:reserved`, `<prefix>:failed`. It uses `BLMOVE` on Redis 6.2 or newer and falls back to `BRPOPLPUSH` on older servers.

#### Templates

- **Component props are escaped.** `alert`, `button` and `card` render `message`, `label` and `body` as text. Pass markup through the component body or a slot.

  ```html
  {# 0.4 #}
  @component('alert', {'message': '<strong>Saved</strong>'})

  {# 0.5 #}
  @component('alert')
      <strong>Saved</strong>
  @endcomponent
  ```

- Link props (`href`, `url`, `action_url`, `brand_url`) accept only `http`, `https`, `mailto`, `tel` and relative URLs. Anything else renders as `#`.
- Components you published with `vendor:publish` keep their old templates until you re-publish with `--force`.
- A directive name used as plain text needs `@@`: write `@@auth` to print `@auth`. Email addresses, Alpine `@click` and CSS `@media` need no change.
- `@for($i = 0; $i < 3; $i++)` now yields 0, 1, 2. It used to include 3.
- `@while` is not supported and raises a `TemplateSyntaxError`.

#### Query builder

- `or_where` follows call order: `where(a).or_where(b).where(c)` is `a OR (b AND c)`. Soft-delete, global-scope and relation constraints always apply on top. Wrap alternatives explicitly when you mean a group:

  ```python
  Post.where("published", True).where_group(lambda q: q.where("title", "a").or_where("title", "b")).get()
  ```

- `where("col", None)` is `IS NULL`. `where("col", "!=", None)` is `IS NOT NULL`. Other operators with `None` raise.
- `Post.where(...).delete()` on a `SoftDeletes` model soft-deletes. Use `force_delete()` to remove rows.
- `find_or_fail()` and `first_or_fail()` raise `HttpException(404)` instead of `ValueError`.
- `save()`, `delete()`, `restore()` and `increment()` return `False` when no row matched.
- `with_("name")` raises `ValueError` unless `name` is a relationship method.
- `update({})`, a negative `limit` or `offset`, and `insert_many` rows with different keys raise `ValueError`. `per_page` is capped at 1000.

#### Sessions and cookies

- The session cookie is `Secure` by default when `APP_ENV` is `production` or unset, and `session.secure` in `config/session.py` is not set. A `config/session.py` generated by 0.4 always sets that key (from `SESSION_SECURE`, off unless `true`), so an upgraded app keeps its current behaviour. To get the new default, change the line to:

  ```python
  "secure": {"true": True, "false": False}.get(os.environ.get("SESSION_SECURE", "").strip().lower()),
  ```
- Sessions are created lazily: a visitor gets a cookie only once something is stored.
- The CSRF token is read from the form field or the `X-CSRF-TOKEN` header, no longer from the query string.
- Logout clears the whole session. Login issues a new CSRF token.
- `SessionRegistry` rows: `row["id"]` is now a hash of the session id. Compare with `public_id_for(session.id)`.
- `hunt session:prune` also deletes the session data of the rows it prunes.

#### Admin panel

- **Sessions, Cache, Logs and Queue pages are off by default.** Enable the ones you want:

  ```python
  Admin.authorize_tool("queue")
  Admin.authorize_tool("logs", lambda request: Auth.user().is_admin)
  ```

- `can_*` policies must return exactly `True`. A truthy value such as a user object is a denial. Policies cannot be `async`; the gate can.
- Show, edit, update and delete pages only reach records that the resource's `index_query` would list. If you narrow `index_query` (for example to a tenant), records outside it now return 404 on those pages too.
- A field declared with a multi-word name and no `attribute=` now maps to the snake_case column: `Text("Cover Photo")` reads `cover_photo`.
- Admin pages do not allow inline scripts. Tool pages rendered in the admin layout (`Admin.render()`) must load their JavaScript from a file. See step 9 for published templates.
- Sorting works only on fields marked `.sortable()`.
- The media manager rejects SVG uploads and has a 10 MB default limit (`Admin.media_max_kb`).
- The Markdown editor has no preview pane.
- `Password` fields are hashed when saved; leaving one blank on edit keeps the current password. If your resource hashed the value itself, remove that code.

#### Mail

- `encryption` accepts `ssl`, `tls` (or `starttls`) and `none`. An empty or unknown value raises. A config without the key defaults to `tls`.
- A mailable whose template fails to render, or whose attachment is missing, raises instead of sending.
- Header values containing a line break, or more than one address in a single value, raise `ValueError`.
- The `log` mailer writes message bodies only when `APP_ENV=local`. Force it either way with `MAIL_LOG_BODY=true` or `false`.

#### Cache

- `Cache.put(key, value, 0)` now forgets the key. Pass `None` for "forever".
- Values must be JSON-safe, or `datetime`, `date`, `time`, `Decimal`, `bytes` or `set`. Other types raise `TypeError` on every driver. This includes model instances: cache `model.to_dict()` (or use the query builder's `remember()`) instead of the model.
- Tuples come back as lists.

#### Middleware that reads the request

The body is now read after routing, and only when something needs it. Middleware you wrote from scratch is unaffected. If you **subclassed** `Authenticate`, `ThrottleRequests`, `VerifyCsrfToken` or another framework middleware and your `handle()` reads `request.input()`, add one line:

```python
class MyAuth(Authenticate):
    reads_body = True
```

A throttle that only overrides `_resolve_key` needs no change.

#### Generators and CLI

- `make:crud` and `make:api` routes require authentication. Pass `--public` to opt out.
- Generated policies and form requests deny by default. Fill in the checks.
- Generator arguments must be valid identifiers. `hunt make:model blog-post` is rejected; use `BlogPost`.
- `hunt key:generate` needs `--force` to replace an existing key, and no longer prints the key.
- `hunt migrate:fresh` drops every table. `migrate:fresh` and `migrate:rollback` need `--force` when `APP_ENV` is `production` or unset.
- `hunt new --force` asks before deleting a directory it did not create.
- `hunt serve:production` refuses to start with `APP_DEBUG=true` unless you pass `--allow-debug`.

### Other behaviour changes

#### HTTP

- Error responses (404, 405, 419, 429, 500) pass through middleware, so they carry the same headers as other responses. With a global CSRF middleware, an unsafe request to an unknown path returns 419 rather than 404.
- `request.input()` and `request.all()` include query-string parameters. Body values win.
- Rate-limit buckets are per route, not per URL. `/users/1` and `/users/2` share a bucket. 429 responses carry `Retry-After`.
- `Router.url()` raises `ValueError` for a missing parameter and percent-encodes values. Registering two routes with the same name raises.
- Literal text in a route is matched literally: `/file.json` no longer matches `/fileXjson`.
- With `TRUSTED_PROXIES=*`, the rightmost `X-Forwarded-For` entry is used. Set `TRUSTED_PROXY_HOPS` if you have more than one proxy.
- Static files are served for `GET` and `HEAD` only. HTML and script files under `/storage/` are sent as downloads.
- Cookie values are percent-encoded when written and decoded by `request.cookie()`.
- A failing startup hook stops the server instead of serving traffic.
- `APP_ENV` defaults to `production` everywhere.
- A `POST` form can carry `_method=PUT`, `PATCH` or `DELETE` to reach those routes. Use the `@method('PUT')` directive.

#### Authentication

- `Gate.define()`, `Gate.before()` and policies must be synchronous. Registering an `async` callback raises `TypeError`.
- Password hashes use a new format. Existing hashes keep working and are upgraded when the user next logs in. The `password` column must hold 72 characters.
- `Auth.attempt()` treats extra keys as conditions: `Auth.attempt({"email": e, "password": p, "active": 1})` only matches active users.
- `Auth.once()` fails for users with two-factor enabled unless you pass `allow_two_factor=True`.
- The token guard reads the token from the query string only with `{"driver": "token", "allow_query": True}`.
- Password reset: other sessions are signed out, the new password needs 8 characters, and a second link for the same address within 60 seconds is not issued.
- Email verification links created by 0.4 stop working. They expire after an hour anyway.
- Two-factor: 5 failed codes lock the account for a growing interval, and a pending challenge expires after 5 minutes.

#### Database and schema

- `transaction()` and `begin()` now include model and query-builder calls made inside them.
- `DATABASE_URL` applies to the default connection only.
- SQLite connections enforce foreign keys.
- New tables: `id()` and `foreign_id()` are `BIGINT`, timestamps are `BIGINT`. On MySQL, a new `foreign_id()` column cannot reference an `id` created by 0.4 with a real foreign key, because the types differ (0.4 created `id` as a signed `INTEGER`); declare that column with `integer()` instead.
- `Schema.table()` creates the indexes and foreign keys you declare. A migration that declared them in an alter callback used to do nothing and now takes effect.
- `Blueprint.primary([...])` creates a primary key.
- Foreign keys are named `<table>_<column>_foreign`.
- `hunt migrate` and `hunt db:*` boot `bootstrap/app.py`, so they use your configured connection.
- `bool(query)` and `len(query)` load the rows.
- `QueryBuilder.remember()` cache keys changed, so cached results from 0.4 are not reused.

#### Helpers

- `env()` converts only booleans, `null`, empty values and plain integers. `"1.5"`, `"1e5"` and `"0123"` stay strings. Use `env_float()`, `env_int()` and `env_bool()`.
- `dd()` raises `DumpAndDie` during a request instead of stopping the worker.
- `Translator.set_locale()` applies to the current request only. Use `set_default_locale()` for the global default.
- `Str.singular("status")` returns `"status"`.

#### Events

- Listeners are keyed by the event's full class path and also receive subclass events.
- Every listener runs even if an earlier one raises. The first error is re-raised afterwards.

#### Notifications

- `notify_now()` sends immediately even for queued notifications.
- Slack webhooks must be `https`, and a failed delivery raises.
- `mark_notification_read()` only marks the notifiable's own notifications.

#### Storage

- S3 `exists()` raises on errors other than "not found".
- S3 `delete()` returns `False` when S3 reports a failure.
- S3 `url()` percent-encodes keys and honours a custom endpoint.
- `LocalDisk.path()` is always absolute.
- An upload stored under an explicit name keeps its extension only when the extension is allowed and matches the content.

#### Logging

- A channel's level applies when the channel is part of a stack.
- An unknown or empty stack member raises `ValueError`.

#### Testing

- `MailFake` and `Mail.fake()` intercept mail. Tests that passed only because nothing was intercepted will now see the mail.
- `freeze_time` returns real `datetime` objects and also freezes `time.time_ns()` and `date.today()`.
- `DatabaseTransactions` rolls back everything a test wrote.

#### Lodge

Re-run `hunt lodge:install` to regenerate `compose.yaml`. Existing credentials in `.env` are kept.

- Service ports are published on `127.0.0.1` only. Change the host port with `FORWARD_DB_PORT`, `FORWARD_REDIS_PORT` and so on.
- Redis requires `REDIS_PASSWORD`, which is generated into `.env`.
- Compose stops with a message when a credential is missing from `.env`.
- Image versions are pinned.

### New settings

| Setting | Default | Purpose |
|---|---|---|
| `SESSION_SECURE` | unset (on in production) | Force the session cookie's `Secure` flag on (`true`) or off (`false`) |
| `BCRYPT_ROUNDS` | 12 | Password hashing cost |
| `QUEUE_RETRY_AFTER` | 90 | Seconds before a reserved job is taken over |
| `QUEUE_PROCESSED_RETENTION` | 604800 | Seconds finished job ids are remembered |
| `MAIL_LOG_BODY` | unset | Force the `log` mailer to write bodies (`true`) or not (`false`); read by the framework unless `log_body` is set in `config/mail.py` |
| `MAX_BODY_SIZE` | 10 MB | Global request body limit; override per route with `.max_body(n)` |
| `TRUSTED_PROXY_HOPS` | unset | Number of proxies in front of the app |
| `SLACK_WEBHOOK_ALLOWED_HOSTS` | unset | Restrict Slack webhook hosts |

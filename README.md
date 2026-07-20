<p align="center">
  <img src="docs/logo.png" alt="hunt framework" width="320" />
</p>

<p align="center">
  <a href="https://github.com/hunt-core/hunt/actions/workflows/tests.yml"><img src="https://github.com/hunt-core/hunt/actions/workflows/tests.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/hunt-core/hunt/actions/workflows/lint.yml"><img src="https://github.com/hunt-core/hunt/actions/workflows/lint.yml/badge.svg" alt="Lint"></a>
  <a href="https://pypi.org/project/hunt-framework/"><img src="https://img.shields.io/pypi/v/hunt-framework" alt="PyPI"></a>
  <a href="https://pypi.org/project/hunt-framework/"><img src="https://img.shields.io/pypi/pyversions/hunt-framework" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/hunt-core/hunt" alt="License"></a>
</p>

A batteries-included Python web framework with Active Record ORM, authentication, admin, queues, migrations, templates, and CLI tooling.

Hunt is built for database-backed web applications that want a cohesive full-stack experience without assembling the stack from separate packages. It also includes first-class tooling for coding agents through `hunt context`, `--json` scaffolding output, and `llms.txt` documentation exports.

## Why Hunt

- ASGI-native request handling with expressive routing and middleware
- Active Record ORM with migrations, relationships, factories, and seeders
- Built-in authentication, sessions, admin panel, queues, mail, and scheduling
- Jinja2-powered templating with Hunt directives and reusable UI components
- CLI workflows for scaffolding, upgrades, diagnostics, and agent-friendly context

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

---

## Installation

```bash
uv pip install hunt-framework
```

Or with pip:

```bash
pip install hunt-framework
```

To install the `hunt` CLI as a global tool:

```bash
uv tool install hunt-framework
```

---

## Creating a new project

```bash
hunt new myapp
cd myapp
uv venv && uv pip install -e .
hunt migrate
hunt serve
```

Visit [http://localhost:8000](http://localhost:8000) - you should see the welcome page.

From there you can add models, authenticated routes, admin resources, queued jobs, and tests without leaving the framework.

---

## Project structure

```text
myapp/
|-- app/
|   |-- admin/             # Admin resources
|   |-- controllers/       # HTTP controllers
|   |   `-- auth/          # Login, register, password controllers
|   |-- middleware/        # Middleware classes
|   |-- models/            # Database models
|   `-- providers/         # Service providers
|-- bootstrap/
|   `-- app.py             # Application bootstrap (ASGI entry point)
|-- config/
|   |-- app.py             # Application settings
|   |-- auth.py            # Auth feature flags
|   |-- database.py        # Database connections
|   |-- filesystems.py     # Storage disks
|   |-- mail.py            # Mail transport
|   `-- view.py            # View/template settings
|-- database/
|   `-- migrations/        # Migration files
|-- resources/
|   `-- views/             # hunt-style HTML templates
|-- routes/
|   |-- admin.py           # Admin panel routes
|   |-- api.py             # API routes
|   |-- auth.py            # Auth routes
|   `-- web.py             # Web routes
|-- storage/               # Logs, compiled views, cache
|-- tests/                 # Test suite
|-- .env                   # Environment variables (not committed)
|-- .env.example           # Environment template
`-- pyproject.toml         # Project dependencies
```

---

## Routing

Define routes in `routes/web.py` or `routes/api.py`:

```python
from hunt.http.router import Router

def register(router: Router) -> None:
    router.get("/", HomeController().index).named("home")
    router.post("/users", UserController().store).named("users.store")
    router.get("/users/{id}", UserController().show).named("users.show")
```

**HTTP methods:** `get`, `post`, `put`, `patch`, `delete`, `any`

**Route groups** with shared prefix and middleware:

```python
with router.group(prefix="/api", middleware=[AuthMiddleware]):
    router.get("/users", UserController().index)
    router.post("/users", UserController().store)
```

---

## Controllers

```bash
hunt make:controller UserController
hunt make:controller UserController --resource   # CRUD methods
hunt make:controller UserController --api        # API resource (no view methods)
```

```python
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response

class UserController(Controller):
    def index(self, request: Request) -> Response:
        users = User.all()
        return self.view("users.index", {"users": users})

    def store(self, request: Request) -> Response:
        data = self.validate(request, {
            "name": "required|string|max:255",
            "email": "required|email",
        })
        user = User.create(data)
        return self.json(user.to_dict(), 201)

    def show(self, request: Request, id: str) -> Response:
        user = User.find_or_fail(id)
        return self.json(user.to_dict())
```

**Response helpers:**

| Method | Description |
|---|---|
| `self.view("template", data)` | Render a hunt template |
| `self.json(data, status)` | JSON response |
| `self.redirect("/url")` | Redirect response |
| `self.response("html", status)` | Plain text/HTML response |

---

## Models

```bash
hunt make:model Post
hunt make:model Post -m   # also creates a migration
```

```python
from hunt.database.model import Model

class Post(Model):
    table = "posts"
    fillable = ["title", "body", "user_id"]
    hidden = ["deleted_at"]
    casts = {"published": "bool"}
    timestamps = True
```

**Querying:**

```python
Post.all()
Post.find(1)
Post.find_or_fail(1)
Post.where("published", True).order_by("created_at", "desc").get()
Post.where("views", ">", 100).limit(10).paginate(per_page=10, page=1)
Post.first_or_create({"slug": "hello-world"}, {"title": "Hello World"})

Post.where_in("status", ["draft", "published"]).get()
Post.where_not_in("role", ["banned"]).get()
Post.where_null("deleted_at").get()
Post.where_not_null("published_at").get()
Post.or_where("status", "archived").get()

# Grouped conditions
Post.where_group(lambda q: q.where("status", "draft").or_where("status", "review")).get()
```

**Creating and saving:**

```python
post = Post.create({"title": "Hello", "body": "World"})

post = Post({"title": "Hello"})
post.body = "World"
post.save()

Post.where("published", False).update({"published": True})
```

**Relationships:**

```python
class Post(Model):
    def author(self):
        return self.belongs_to(User)

    def comments(self):
        return self.has_many(Comment)
```

---

## Migrations

```bash
hunt make:migration create_posts_table
hunt make:migration add_published_to_posts --table=posts
```

```python
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration

class CreatePostsTable(Migration):
    def up(self) -> None:
        Schema.create("posts", lambda bp: [
            bp.id(),
            bp.uuid("public_id"),
            bp.string("title"),
            bp.text("body").nullable(),
            bp.boolean("published").default(False),
            bp.foreign_id("user_id"),
            bp.timestamps(),
        ])

    def down(self) -> None:
        Schema.drop_if_exists("posts")
```

**Altering tables:**

```python
Schema.table("posts", lambda bp: [
    bp.string("subtitle").nullable(),        # add column
    bp.drop_column("legacy_field"),          # drop column (raises if absent)
    bp.drop_column_if_exists("old_field"),   # safe drop - idempotent
    bp.rename_column("body", "content"),     # rename column
])
```

**Running migrations:**

```bash
hunt migrate          # run pending migrations
hunt migrate:status   # show migration status
hunt migrate:rollback # rollback last batch
hunt migrate:fresh    # drop all tables and re-run
```

---

## Templates

Templates live in `resources/views/` and use hunt-style syntax. Files use the `.html` extension.

**`resources/views/layout.html`**

```html
<!DOCTYPE html>
<html>
<head><title>@yield('title', 'My App')</title></head>
<body>@yield('content')</body>
</html>
```

**`resources/views/posts/index.html`**

```html
@extends('layout')

@section('content')
<h1>Posts</h1>

@foreach($posts as $post)
    <article>
        <h2>{{ $post.title }}</h2>
        <p>{{ $post.body }}</p>
    </article>
@endforeach
@endsection
```

**Supported directives:**

| Directive | Description |
|---|---|
| `@extends('layout')` | Inherit a parent layout |
| `@section('name')` / `@endsection` | Define a content block |
| `@yield('name')` | Output a block |
| `@include('partial')` | Include a sub-template |
| `@foreach($items as $item)` / `@endforeach` | Loop |
| `@if($condition)` / `@elseif` / `@else` / `@endif` | Conditionals |
| `@unless($condition)` / `@endunless` | Negated conditional |
| `{{ $variable }}` | Escaped output |
| `{!! $html !!}` | Raw (unescaped) output |
| `@csrf` | CSRF hidden input |
| `@error('field')` / `@enderror` | Show validation errors |
| `@auth` / `@endauth` | Authenticated user block |
| `@guest` / `@endguest` | Guest user block |
| `{{-- comment --}}` | Template comment (not rendered) |

### Overriding framework views

Publish the built-in auth and component templates into your project to customise them:

```bash
hunt vendor:publish                        # all framework views
hunt vendor:publish --tag views:auth       # auth views only
hunt vendor:publish --tag views:components # UI components only
hunt vendor:publish --force                # overwrite existing files
```

Files are copied to `resources/views/`. Any file present there takes priority over the framework's built-in copy. If a file already exists when publishing, the framework copy is placed in `resources/views/framework/` so you can reference it without losing your changes.

---

## Authentication

`hunt new` scaffolds a complete auth system: login, registration, and password reset - all wired up and ready to use.

```python
from hunt.auth.manager import Auth

Auth.attempt({"email": "...", "password": "..."})  # login + session
Auth.login(user)     # log in without credential check
Auth.logout()        # clear session
Auth.check()         # True if authenticated
Auth.user()          # current User instance or None
Auth.id()            # current user's primary key or None
```

Protect routes with the included `Authenticate` middleware:

```python
from hunt.http.middleware.authenticate import Authenticate

router.get("/dashboard", DashboardController().index).middleware(Authenticate)
```

### Two-factor authentication

Add TOTP-based 2FA to any application with one command:

```bash
hunt make:2fa-controllers
```

This scaffolds routes, controllers, Tailwind-styled templates, and a migration that adds `two_factor_secret`, `two_factor_enabled`, and `two_factor_recovery_codes` columns to the `users` table. After running `hunt migrate`, protect any route group with the included middleware:

```python
from hunt.http.middleware.two_factor import EnsureTwoFactorAuthenticated

with router.group(middleware=[Authenticate, EnsureTwoFactorAuthenticated]):
    router.get("/dashboard", DashboardController().index)
```

Users who have 2FA enabled are redirected to `/two-factor/challenge` after login. Recovery codes are generated automatically during setup.

### Feature flags

`config/auth.py` controls which auth features are active. Set any flag to `False` to remove those routes entirely (returns 404) and hide the corresponding links in the built-in auth views:

```python
config = {
    "features": {
        "registration": True,
        "login": True,
        "forgot_password": True,
    }
}
```

---

## Admin panel

The admin panel is available at `/hunt-admin` after registration. Define resources in `app/admin/`:

```python
from hunt.admin import AdminResource
from hunt.admin.fields import Text, Email, Boolean, DateTime

class UserResource(AdminResource):
    model = User
    label = "User"
    search_columns = ["name", "email"]

    def fields(self):
        return [
            Text("Name", attribute="name").rules("required", "max:255").sortable(),
            Email("Email", attribute="email").rules("required", "email"),
            Boolean("Admin", attribute="is_admin"),
            DateTime("Created At", attribute="created_at").hide_from_forms(),
        ]
```

Register in `routes/admin.py`:

```python
from hunt.admin import Admin
from app.admin.user_resource import UserResource

Admin.resource(UserResource)
Admin.register_to(router)
```

**Available field types:** `Text`, `Email`, `Password`, `Textarea`, `RichText`, `Number`, `Boolean`, `Select`, `Image`, `DateTime`, `BelongsTo`, `HasMany`, `Badge`

**Filters:**

```python
from hunt.admin import SelectFilter, BooleanFilter, DateRangeFilter

class StatusFilter(SelectFilter):
    name = "Status"
    attribute = "status"

    def options(self):
        return [("active", "Active"), ("inactive", "Inactive")]

class CreatedAtFilter(DateRangeFilter):
    name = "Created At"
    attribute = "created_at"
```

**Actions:**

```python
from hunt.admin import Action, ActionResponse

class ActivateUsers(Action):
    name = "Activate"

    def handle(self, request, models):
        for user in models:
            user.status = "active"
            user.save()
        return ActionResponse.success(f"{len(models)} user(s) activated.")
```

Built-in actions: `BulkDeleteAction`, `RestoreAction` (soft deletes), `ExportCsvAction` (CSV download).

`ActionResponse` types: `.success(text)`, `.error(text)`, `.redirect(url)`, `.download(content, filename)`.

**Customizing templates:**

```bash
hunt admin:publish          # copy all admin templates to resources/views/admin/
hunt admin:publish --force  # overwrite existing

hunt vendor:publish --tag views:auth       # customise login, register, password reset
hunt vendor:publish --tag views:components # customise alert, button, card, etc.
```

---

## Validation

```python
data = self.validate(request, {
    "name":     "required|string|max:255",
    "email":    "required|email|unique:users,email",
    "password": "required|min:8|confirmed",
    "role":     "required|in:admin,editor,viewer",
})
```

**Available rules:**

`required` · `string` · `integer` · `numeric` · `boolean` · `email` · `url`
· `min:n` · `max:n` · `size:n` · `in:a,b,c` · `not_in:a,b,c`
· `confirmed` · `regex:pattern` · `unique:table,column` · `array`

---

## Middleware

```bash
hunt make:middleware AuthMiddleware
```

```python
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

class AuthMiddleware(Middleware):
    async def handle(self, request: Request, next: Next) -> Response:
        token = request.bearer_token()
        if not token:
            from hunt.http.response import JsonResponse
            return JsonResponse({"error": "Unauthenticated"}, 401)
        return await next(request)
```

### Built-in middleware

| Middleware | Import | Purpose |
|---|---|---|
| `Authenticate` | `hunt.http.middleware.authenticate` | Redirect unauthenticated requests to `/login` |
| `SecureHeaders` | `hunt.http.middleware.secure_headers` | Add `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, opt-in HSTS and CSP |
| `TrustProxies` | `hunt.http.middleware.trust_proxies` | Rewrite `request.ip` and `request.scheme` from `X-Forwarded-For` / `X-Forwarded-Proto` when behind a proxy or load balancer |
| `MaintenanceMode` | `hunt.http.middleware.maintenance` | Return 503 with `Retry-After` when `.maintenance` sentinel file is present |
| `CompressResponse` | `hunt.http.middleware.compress` | Gzip responses for clients that send `Accept-Encoding: gzip`; skips small, already-encoded, or non-text responses; sets `Vary: Accept-Encoding` |
| `EnsureTwoFactorAuthenticated` | `hunt.http.middleware.two_factor` | Redirect users who have 2FA enabled but haven't completed the challenge |

---

## CLI reference

```bash
# Application
hunt new <name>                      # scaffold a new application
hunt upgrade                         # pull in new scaffold files to existing app
hunt serve                           # start the dev server (auto-reload)
hunt serve:production                # start a production-grade uvicorn server
hunt tinker                          # interactive REPL with app bootstrapped
hunt key:generate                    # generate and write a new APP_KEY
hunt down [--message "..."] [--retry 60]  # enable maintenance mode (503)
hunt up                              # disable maintenance mode

# Routes
hunt route:list                      # print all registered routes

# Migrations
hunt migrate                         # run pending migrations
hunt migrate:rollback                # rollback last batch
hunt migrate:fresh                   # drop all tables and re-run
hunt migrate:status                  # show migration status
hunt migrate:status --pending        # exit 1 if any migrations are pending (CI/CD gate)

# Database
hunt db:seed                         # run database seeders
hunt db:seed --class PostSeeder      # run a specific seeder

# Cache
hunt cache:clear                     # clear all cached values
hunt cache:forget <key>              # remove a single cache key

# Queue
hunt queue:work                      # start the queue worker
hunt queue:work --queue high --tries 3
hunt queue:failed                    # list failed jobs
hunt queue:retry <id>                # re-queue a failed job
hunt queue:flush                     # delete all failed jobs
hunt queue:table                     # create the jobs migration

# Jobs
hunt job:list                        # list all discovered Job classes
hunt job:run <name>                  # run a job synchronously
hunt job:run <name> --data key=value

# Scheduler
hunt schedule:run                    # run due scheduled tasks (call from cron)
hunt schedule:list                   # list all scheduled tasks

# Code generation
hunt make:model <Name>               # app/models/name.py
hunt make:model <Name> -m            # model + migration
hunt make:controller <Name>          # app/controllers/name_controller.py
hunt make:controller <Name> --resource
hunt make:migration <name>           # database/migrations/TIMESTAMP_name.py
hunt make:middleware <Name>          # app/middleware/name.py
hunt make:request <Name>             # app/requests/name_request.py
hunt make:event <Name>               # app/events/name.py
hunt make:listener <Name>            # app/listeners/name.py
hunt make:mail <Name>                # app/mail/name.py
hunt make:notification <Name>        # app/notifications/name.py
hunt make:seeder <Name>              # database/seeders/NameSeeder.py
hunt make:factory <Name>             # database/factories/NameFactory.py
hunt make:job <Name>                 # app/jobs/name.py
hunt make:command <Name>             # app/console/commands/name.py
hunt make:admin-resource <Model>     # app/admin/model_resource.py
hunt make:2fa-controllers            # 2FA routes, controllers, templates, migration

# Admin
hunt admin:publish                   # copy admin templates to resources/views/admin/

# Views
hunt vendor:publish                        # all framework views -> resources/views/
hunt vendor:publish --tag views:auth       # auth views only
hunt vendor:publish --tag views:components # UI components only
hunt vendor:publish --force                # overwrite existing files
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `hunt` | Application name |
| `APP_ENV` | `production` | Environment (`local`, `production`) |
| `APP_DEBUG` | `false` | Enable debug mode |
| `APP_URL` | `http://localhost:8000` | Application URL |
| `APP_KEY` | - | Encryption key (auto-generated by `hunt new`) |
| `DB_CONNECTION` | `sqlite` | Driver (`sqlite`, `mysql`, `postgresql`) |
| `DB_HOST` | `127.0.0.1` | Database host |
| `DB_PORT` | `3306` / `5432` | Database port |
| `DB_DATABASE` | - | Database name / SQLite file path |
| `DB_USERNAME` | - | Database username |
| `DB_PASSWORD` | - | Database password |
| `TRUSTED_PROXIES` | - | Comma-separated IPs/CIDRs to trust for `X-Forwarded-*` headers; `*` to trust all (dev only) |
| `MAX_BODY_SIZE` | `10485760` | Max request body in bytes (default 10 MB); requests over the limit return 413 |
| `SECURE_HSTS_SECONDS` | `0` | Enable `Strict-Transport-Security` with this max-age; `0` disables |
| `SECURE_CONTENT_SECURITY_POLICY` | - | Value for the `Content-Security-Policy` header |
| `ACCESS_LOG` | `true` | Set to `false` to skip per-request access log lines (e.g. when a reverse proxy already logs) |
| `STATIC_CACHE_CONTROL` | `public, max-age=3600` | `Cache-Control` header sent with static file responses |
| `STATIC_EXTENSIONS` | *(built-in allowlist)* | Comma-separated list of file extensions to serve as static files; replaces the default allowlist |
| `OFFLOAD_SYNC_HANDLERS` | `false` | Run synchronous route handlers in a worker thread via `asyncio.to_thread` to avoid blocking the event loop |
| `HEALTH_CHECK_VERBOSE` | `false` | Include the framework version in the `/health` response payload |
| `LOG_NON_BLOCKING` | `true` | Route file/daily log writes through a background thread queue so disk I/O doesn't block the event loop; set to `false` to disable |
| `GZIP_ENABLED` | `true` | Enable gzip compression when `CompressResponse` middleware is in the stack |
| `GZIP_MIN_LENGTH` | `1024` | Minimum response body size in bytes before gzip is applied |
| `GZIP_LEVEL` | `6` | zlib compression level (1-9) used by `CompressResponse` |

---

## Testing

```python
import pytest
from hunt.testing.test_case import HuntTestCase
from hunt.http.router import Router
from hunt.http.kernel import HttpKernel

class TestMyApp(HuntTestCase):
    def setup_method(self):
        router = Router()
        router.get("/users/{id}", lambda req, id: {"id": id})
        self.kernel = HttpKernel(router)

    @pytest.mark.asyncio
    async def test_get_user(self):
        resp = await self.get("/users/42")
        resp.assert_ok().assert_json("id", "42")
```

```bash
pytest
```

---

## Documentation

- Quick start: [hunt-framework.com/docs/installation](https://hunt-framework.com/docs/installation)
- AI agents: [hunt-framework.com/docs/ai-agents](https://hunt-framework.com/docs/ai-agents)
- Full docs: [hunt-framework.com](https://hunt-framework.com)

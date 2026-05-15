# hunt

[![CI](https://github.com/hunt-core/hunt/actions/workflows/tests.yml/badge.svg)](https://github.com/hunt-core/hunt/actions/workflows/tests.yml)
[![Lint](https://github.com/hunt-core/hunt/actions/workflows/lint.yml/badge.svg)](https://github.com/hunt-core/hunt/actions/workflows/lint.yml)
[![PyPI](https://img.shields.io/pypi/v/hunt-framework)](https://pypi.org/project/hunt-framework/)
[![Python](https://img.shields.io/pypi/pyversions/hunt-framework)](https://pypi.org/project/hunt-framework/)
[![License](https://img.shields.io/github/license/hunt-core/hunt)](LICENSE)

A Python web framework. Routing, ORM, templates, migrations, validation, authentication, admin panel, and a CLI — all in one package.

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

Visit [http://localhost:8000](http://localhost:8000) — you should see the welcome page.

---

## Project structure

```
myapp/
├── app/
│   ├── admin/             # Admin resources
│   ├── controllers/       # HTTP controllers
│   │   └── auth/          # Login, register, password controllers
│   ├── middleware/        # Middleware classes
│   ├── models/            # Database models
│   └── providers/         # Service providers
├── bootstrap/
│   └── app.py             # Application bootstrap (ASGI entry point)
├── config/
│   ├── app.py             # Application settings
│   ├── auth.py            # Auth feature flags
│   ├── database.py        # Database connections
│   ├── filesystems.py     # Storage disks
│   ├── mail.py            # Mail transport
│   └── view.py            # View/template settings
├── database/
│   └── migrations/        # Migration files
├── resources/
│   └── views/             # hunt-style HTML templates
├── routes/
│   ├── admin.py           # Admin panel routes
│   ├── api.py             # API routes
│   ├── auth.py            # Auth routes
│   └── web.py             # Web routes
├── storage/               # Logs, compiled views, cache
├── tests/                 # Test suite
├── .env                   # Environment variables (not committed)
├── .env.example           # Environment template
└── pyproject.toml         # Project dependencies
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
            bp.string("title"),
            bp.text("body").nullable(),
            bp.boolean("published").default(False),
            bp.foreign_id("user_id"),
            bp.timestamps(),
        ])

    def down(self) -> None:
        Schema.drop_if_exists("posts")
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

---

## Authentication

`hunt new` scaffolds a complete auth system: login, registration, and password reset — all wired up and ready to use.

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
from hunt.admin.filter import SelectFilter, BooleanFilter

class StatusFilter(SelectFilter):
    name = "Status"
    attribute = "status"

    def options(self):
        return [("active", "Active"), ("inactive", "Inactive")]
```

**Actions:**

```python
from hunt.admin.action import Action, ActionResponse

class ActivateUsers(Action):
    name = "Activate"

    def handle(self, request, models):
        for user in models:
            user._attributes["status"] = "active"
            user.save()
        return ActionResponse.success(f"{len(models)} user(s) activated.")
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

---

## CLI reference

```bash
hunt new <name>               # scaffold a new application
hunt upgrade                  # add missing scaffold files to an existing app
hunt serve                    # start the development server
hunt serve --port 3000        # custom port
hunt serve --host 0.0.0.0     # bind to all interfaces
hunt tinker                   # interactive REPL

hunt make:model <Name>        # create a model
hunt make:model <Name> -m     # model + migration
hunt make:controller <Name>   # create a controller
hunt make:controller <Name> --resource  # CRUD controller
hunt make:controller <Name> --api       # API controller
hunt make:migration <name>    # create a migration
hunt make:middleware <Name>   # create a middleware

hunt migrate                  # run pending migrations
hunt migrate:rollback         # rollback last batch
hunt migrate:fresh            # drop all + re-run
hunt migrate:status           # show migration status

hunt route:list               # list all registered routes
hunt key:generate             # generate a new APP_KEY
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `hunt` | Application name |
| `APP_ENV` | `production` | Environment (`local`, `production`) |
| `APP_DEBUG` | `false` | Enable debug mode |
| `APP_URL` | `http://localhost:8000` | Application URL |
| `APP_KEY` | — | Encryption key (auto-generated by `hunt new`) |
| `DB_CONNECTION` | `sqlite` | Driver (`sqlite`, `mysql`, `postgresql`) |
| `DB_HOST` | `127.0.0.1` | Database host |
| `DB_PORT` | `3306` / `5432` | Database port |
| `DB_DATABASE` | — | Database name / SQLite file path |
| `DB_USERNAME` | — | Database username |
| `DB_PASSWORD` | — | Database password |

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

Full documentation at [hunt-framework.com](https://hunt-framework.com)

# hunt

A Python web framework. Routing, ORM, hunt templates, migrations, validation, and a CLI — all in one package.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

---

## Installation

Install directly from GitHub (recommended while hunt is in early development):

```bash
uv pip install git+ssh://git@github.com/hunt-core/hunt.git
```

Or with pip:

```bash
pip install git+ssh://git@github.com/hunt-core/hunt.git
```

To pin to a specific tag or commit:

```bash
uv pip install git+ssh://git@github.com/hunt-core/hunt.git@v0.1.0
uv pip install git+ssh://git@github.com/hunt-core/hunt.git@abc1234
```

To install the `hunt` CLI as a global tool:

```bash
uv tool install git+ssh://git@github.com/hunt-core/hunt.git
```

---

## Creating a new project

```bash
hunt new myapp
cd myapp
```

Set up a virtual environment and install dependencies:

```bash
uv venv
uv pip install -e .
```

Copy and configure your environment file:

```bash
cp .env.example .env
```

Run the development server:

```bash
hunt serve
```

Visit [http://localhost:8000](http://localhost:8000) — you should see the welcome page.

---

## Project structure

```
myapp/
├── app/
│   ├── controllers/       # HTTP controllers
│   ├── models/            # Database models
│   ├── middleware/        # Middleware classes
│   └── providers/         # Service providers
├── bootstrap/
│   └── app.py             # Application bootstrap (ASGI entry point)
├── config/
│   ├── app.py             # Application settings
│   ├── database.py        # Database connections
│   └── view.py            # View/template settings
├── database/
│   └── migrations/        # Migration files
├── resources/
│   └── views/             # hunt-style HTML templates
├── routes/
│   ├── web.py             # Web routes
│   └── api.py             # API routes
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

class User(Model):
    def posts(self):
        return self.has_many(Post)

    def profile(self):
        return self.has_one(Profile)
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
<head>
    <title>@yield('title', 'My App')</title>
</head>
<body>
    @yield('content')
</body>
</html>
```

**`resources/views/posts/index.html`**

```html
@extends('layout')

@section('title')
All Posts
@endsection

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

**Standalone validator:**

```python
from hunt.validation.validator import Validator

v = Validator.make(request.all(), {"email": "required|email"})

if v.fails():
    return self.json({"errors": v.errors().all()}, 422)

data = v.validate()  # raises ValidationException on failure
```

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

Apply to individual routes or groups:

```python
router.get("/dashboard", DashboardController().index).middleware_list(AuthMiddleware)

with router.group(middleware=[AuthMiddleware]):
    router.get("/profile", ProfileController().show)
    router.post("/profile", ProfileController().update)
```

---

## CLI reference

```bash
hunt new <name>               # scaffold a new application
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
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `hunt` | Application name |
| `APP_ENV` | `production` | Environment (`local`, `production`) |
| `APP_DEBUG` | `false` | Enable debug mode |
| `APP_URL` | `http://localhost:8000` | Application URL |
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

    @pytest.mark.asyncio
    async def test_create_user(self):
        resp = await self.post("/users", json={"name": "Alice", "email": "alice@example.com"})
        resp.assert_created()
        self.assert_database_has("users", {"email": "alice@example.com"})
```

Run the test suite:

```bash
pytest
```

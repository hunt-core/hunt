"""Blog starter kit — Post / Category / Tag models, CRUD controller, views, routes."""
from __future__ import annotations

from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def apply(target: Path) -> None:
    """Overlay blog starter kit files on the base skeleton."""
    _write(target / "app" / "models" / "post.py", _POST_MODEL)
    _write(target / "app" / "models" / "category.py", _CATEGORY_MODEL)
    _write(target / "app" / "models" / "tag.py", _TAG_MODEL)
    _write(target / "app" / "controllers" / "post_controller.py", _POST_CONTROLLER)
    _write(target / "app" / "admin" / "post_resource.py", _POST_ADMIN)
    _write(target / "app" / "admin" / "category_resource.py", _CATEGORY_ADMIN)
    _write(target / "database" / "migrations" / "0100_create_categories_table.py", _MIG_CATEGORIES)
    _write(target / "database" / "migrations" / "0101_create_posts_table.py", _MIG_POSTS)
    _write(target / "database" / "migrations" / "0102_create_tags_table.py", _MIG_TAGS)
    _write(target / "database" / "migrations" / "0103_create_post_tag_table.py", _MIG_POST_TAG)
    _write(target / "database" / "factories" / "post_factory.py", _POST_FACTORY)
    _write(target / "database" / "factories" / "category_factory.py", _CATEGORY_FACTORY)
    _write(target / "resources" / "views" / "layout.html", _BLOG_LAYOUT)
    _write(target / "resources" / "views" / "posts" / "index.html", _POSTS_INDEX)
    _write(target / "resources" / "views" / "posts" / "show.html", _POSTS_SHOW)
    _write(target / "resources" / "views" / "posts" / "create.html", _POSTS_CREATE)
    _write(target / "resources" / "views" / "posts" / "edit.html", _POSTS_EDIT)
    _write(target / "routes" / "web.py", _BLOG_ROUTES_WEB)
    _write(target / "routes" / "admin.py", _BLOG_ROUTES_ADMIN)
    _write(target / "README.md", _README)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_POST_MODEL = """\
from hunt.database.model import Model


class Post(Model):
    table = "posts"
    fillable = ["title", "slug", "body", "published", "category_id"]
    casts = {"published": "boolean"}

    def category(self):
        from app.models.category import Category
        cid = self._attributes.get("category_id")
        return Category.find(cid) if cid else None

    def tags(self):
        from hunt.database.connection import connection
        from sqlalchemy import text
        from app.models.tag import Tag
        engine = connection()
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT tag_id FROM post_tag WHERE post_id = :pid"),
                {"pid": self._attributes.get("id")},
            ).fetchall()
        ids = [r[0] for r in rows]
        return [Tag.find(tid) for tid in ids] if ids else []
"""

_CATEGORY_MODEL = """\
from hunt.database.model import Model


class Category(Model):
    table = "categories"
    fillable = ["name", "slug"]

    def posts(self):
        from app.models.post import Post
        return Post.where("category_id", self._attributes.get("id")).all()
"""

_TAG_MODEL = """\
from hunt.database.model import Model


class Tag(Model):
    table = "tags"
    fillable = ["name", "slug"]
"""

# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

_POST_CONTROLLER = """\
import re

from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.validation.validator import Validator
from app.models.post import Post
from app.models.category import Category


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class PostController(Controller):
    def index(self, request: Request) -> Response:
        posts = Post.where("published", True).all()
        return self.view("posts.index", {"posts": posts})

    def show(self, request: Request, id: int) -> Response:
        post = Post.find(id)
        return self.view("posts.show", {"post": post})

    def create(self, request: Request) -> Response:
        return self.view("posts.create", {
            "categories": Category.all(),
            "errors": {},
            "old": {},
        })

    def store(self, request: Request) -> Response:
        data = {
            "title": request.input("title", ""),
            "body": request.input("body", ""),
            "category_id": request.input("category_id") or None,
            "published": bool(request.input("published")),
        }
        v = Validator.make(data, {"title": "required|string|max:255", "body": "required"})
        if v.fails():
            return self.view("posts.create", {
                "categories": Category.all(),
                "errors": v.errors()._errors,
                "old": data,
            })
        Post.create({**data, "slug": _slugify(data["title"])})
        return RedirectResponse("/posts")

    def edit(self, request: Request, id: int) -> Response:
        post = Post.find(id)
        return self.view("posts.edit", {
            "post": post,
            "categories": Category.all(),
            "errors": {},
            "old": {},
        })

    def update(self, request: Request, id: int) -> Response:
        post = Post.find(id)
        data = {
            "title": request.input("title", ""),
            "body": request.input("body", ""),
            "category_id": request.input("category_id") or None,
            "published": bool(request.input("published")),
        }
        v = Validator.make(data, {"title": "required|string|max:255", "body": "required"})
        if v.fails():
            return self.view("posts.edit", {
                "post": post,
                "categories": Category.all(),
                "errors": v.errors()._errors,
                "old": data,
            })
        for k, val in {**data, "slug": _slugify(data["title"])}.items():
            post._attributes[k] = val
        post.save()
        return RedirectResponse(f"/posts/{id}")

    def destroy(self, request: Request, id: int) -> Response:
        Post.find(id).delete()
        return RedirectResponse("/posts")
"""

# ---------------------------------------------------------------------------
# Admin resources
# ---------------------------------------------------------------------------

_POST_ADMIN = """\
from hunt.admin import AdminResource
from hunt.admin.fields import Text, Boolean, DateTime
from app.models.post import Post


class PostResource(AdminResource):
    model = Post
    label = "Post"
    search_columns = ["title", "slug"]
    default_order = ("created_at", "desc")
    per_page = 20

    def fields(self):
        return [
            Text("Id", attribute="id").readonly().sortable(),
            Text("Title", attribute="title").rules("required", "string", "max:255").sortable(),
            Text("Slug", attribute="slug").rules("required").sortable(),
            Text("Body", attribute="body").rules("required"),
            Boolean("Published", attribute="published"),
            DateTime("Created At", attribute="created_at").sortable().hide_from_forms(),
        ]

    def can_view_any(self, request) -> bool:
        return True

    def can_create(self, request) -> bool:
        return True

    def can_update(self, request, instance=None) -> bool:
        return True

    def can_delete(self, request, instance=None) -> bool:
        return True
"""

_CATEGORY_ADMIN = """\
from hunt.admin import AdminResource
from hunt.admin.fields import Text, DateTime
from app.models.category import Category


class CategoryResource(AdminResource):
    model = Category
    label = "Category"
    search_columns = ["name", "slug"]
    per_page = 20

    def fields(self):
        return [
            Text("Id", attribute="id").readonly().sortable(),
            Text("Name", attribute="name").rules("required", "string", "max:255").sortable(),
            Text("Slug", attribute="slug").rules("required").sortable(),
            DateTime("Created At", attribute="created_at").sortable().hide_from_forms(),
        ]

    def can_view_any(self, request) -> bool:
        return True

    def can_create(self, request) -> bool:
        return True

    def can_update(self, request, instance=None) -> bool:
        return True

    def can_delete(self, request, instance=None) -> bool:
        return True
"""

# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_MIG_CATEGORIES = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateCategoriesTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.string("name")
            table.string("slug").unique()
            table.timestamps()

        Schema.create("categories", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("categories")
"""

_MIG_POSTS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreatePostsTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.integer("category_id").nullable()
            table.string("title")
            table.string("slug").unique()
            table.text("body")
            table.boolean("published").default(False)
            table.timestamps()

        Schema.create("posts", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("posts")
"""

_MIG_TAGS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateTagsTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.string("name")
            table.string("slug").unique()
            table.timestamps()

        Schema.create("tags", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("tags")
"""

_MIG_POST_TAG = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreatePostTagTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.integer("post_id")
            table.integer("tag_id")
            table.index(["post_id", "tag_id"])

        Schema.create("post_tag", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("post_tag")
"""

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

_POST_FACTORY = """\
import random
import re

from hunt.database.factory import Factory


class PostFactory(Factory):
    _counter = 0

    @classmethod
    def definition(cls) -> dict:
        cls._counter += 1
        n = cls._counter
        title = f"Sample Post {n}"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return {
            "title": title,
            "slug": slug,
            "body": f"This is the body of sample post {n}.",
            "published": random.choice([True, False]),
            "category_id": None,
        }
"""

_CATEGORY_FACTORY = """\
from hunt.database.factory import Factory


class CategoryFactory(Factory):
    _counter = 0

    @classmethod
    def definition(cls) -> dict:
        cls._counter += 1
        n = cls._counter
        name = f"Category {n}"
        return {
            "name": name,
            "slug": name.lower().replace(" ", "-"),
        }
"""

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

_BLOG_LAYOUT = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ page_title if page_title is defined else (config('app.name', 'Blog') if config is defined else 'Blog') }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-900 min-h-screen flex flex-col">
    <nav class="bg-white shadow-sm border-b">
        <div class="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
            <a href="/" class="text-xl font-bold text-indigo-600">
                {{ config('app.name', 'Blog') if config is defined else 'Blog' }}
            </a>
            <div class="flex gap-4 items-center text-sm">
                <a href="/posts" class="text-gray-600 hover:text-indigo-600">Posts</a>
                {% if auth_check is defined and auth_check() %}
                    <a href="/posts/create" class="text-gray-600 hover:text-indigo-600">New Post</a>
                    <form method="POST" action="/logout" class="inline">
                        @csrf
                        <button type="submit" class="text-gray-600 hover:text-indigo-600">Logout</button>
                    </form>
                {% else %}
                    <a href="/login" class="text-gray-600 hover:text-indigo-600">Login</a>
                    <a href="/register" class="bg-indigo-600 text-white px-3 py-1.5 rounded-md hover:bg-indigo-700">Register</a>
                {% endif %}
            </div>
        </div>
    </nav>
    <main class="flex-1 max-w-4xl mx-auto px-4 py-8 w-full">
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-t py-4 text-center text-sm text-gray-400">
        Built with <a href="https://hunt-framework.com" class="text-indigo-500">hunt</a>
    </footer>
</body>
</html>
"""

_POSTS_INDEX = """\
@extends('layout')

@section('content')
<div class="flex items-center justify-between mb-8">
    <h1 class="text-3xl font-bold">Posts</h1>
    {% if auth_check is defined and auth_check() %}
    <a href="/posts/create" class="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 text-sm">New Post</a>
    {% endif %}
</div>

{% if posts %}
<div class="space-y-6">
    {% for post in posts %}
    <article class="bg-white rounded-lg shadow-sm border p-6">
        <h2 class="text-xl font-semibold mb-2">
            <a href="/posts/{{ post._attributes.id }}" class="hover:text-indigo-600">{{ post._attributes.title }}</a>
        </h2>
        <p class="text-gray-600 text-sm mb-4">{{ post._attributes.body[:200] }}{% if post._attributes.body|length > 200 %}...{% endif %}</p>
        <div class="flex gap-3">
            <a href="/posts/{{ post._attributes.id }}" class="text-indigo-600 text-sm hover:underline">Read more</a>
            {% if auth_check is defined and auth_check() %}
            <a href="/posts/{{ post._attributes.id }}/edit" class="text-gray-500 text-sm hover:underline">Edit</a>
            <form method="POST" action="/posts/{{ post._attributes.id }}/delete" class="inline">
                @csrf
                <button type="submit" class="text-red-500 text-sm hover:underline" onclick="return confirm('Delete this post?')">Delete</button>
            </form>
            {% endif %}
        </div>
    </article>
    {% endfor %}
</div>
{% else %}
<div class="text-center py-16 text-gray-400">
    <p class="text-lg">No posts yet.</p>
    {% if auth_check is defined and auth_check() %}
    <a href="/posts/create" class="mt-4 inline-block text-indigo-600 hover:underline">Create the first post</a>
    {% endif %}
</div>
{% endif %}
@endsection
"""

_POSTS_SHOW = """\
@extends('layout')

@section('content')
<article class="bg-white rounded-lg shadow-sm border p-8">
    <h1 class="text-3xl font-bold mb-4">{{ post._attributes.title }}</h1>
    <div class="prose max-w-none text-gray-700 whitespace-pre-wrap">{{ post._attributes.body }}</div>
    <div class="mt-8 flex gap-3 pt-6 border-t">
        <a href="/posts" class="text-gray-500 text-sm hover:underline">Back to posts</a>
        {% if auth_check is defined and auth_check() %}
        <a href="/posts/{{ post._attributes.id }}/edit" class="text-indigo-600 text-sm hover:underline">Edit</a>
        <form method="POST" action="/posts/{{ post._attributes.id }}/delete" class="inline">
            @csrf
            <button type="submit" class="text-red-500 text-sm hover:underline" onclick="return confirm('Delete this post?')">Delete</button>
        </form>
        {% endif %}
    </div>
</article>
@endsection
"""

_POSTS_CREATE = """\
@extends('layout')

@section('content')
<div class="bg-white rounded-lg shadow-sm border p-8 max-w-2xl">
    <h1 class="text-2xl font-bold mb-6">New Post</h1>

    @errors

    <form method="POST" action="/posts" class="space-y-5">
        @csrf
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Title</label>
            <input type="text" name="title" value="@old('title')"
                   class="w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500">
            {% if errors.title %}<p class="text-red-500 text-xs mt-1">{{ errors.title[0] }}</p>{% endif %}
        </div>
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Body</label>
            <textarea name="body" rows="10"
                      class="w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500">@old('body')</textarea>
            {% if errors.body %}<p class="text-red-500 text-xs mt-1">{{ errors.body[0] }}</p>{% endif %}
        </div>
        {% if categories %}
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Category</label>
            <select name="category_id" class="w-full border border-gray-300 rounded-md px-3 py-2">
                <option value="">— None —</option>
                {% for cat in categories %}
                <option value="{{ cat._attributes.id }}">{{ cat._attributes.name }}</option>
                {% endfor %}
            </select>
        </div>
        {% endif %}
        <div class="flex items-center gap-2">
            <input type="checkbox" name="published" id="published" value="1" class="rounded">
            <label for="published" class="text-sm text-gray-700">Published</label>
        </div>
        <div class="flex gap-3">
            <button type="submit" class="bg-indigo-600 text-white px-5 py-2 rounded-md hover:bg-indigo-700">Create Post</button>
            <a href="/posts" class="text-gray-500 px-5 py-2 hover:underline">Cancel</a>
        </div>
    </form>
</div>
@endsection
"""

_POSTS_EDIT = """\
@extends('layout')

@section('content')
<div class="bg-white rounded-lg shadow-sm border p-8 max-w-2xl">
    <h1 class="text-2xl font-bold mb-6">Edit Post</h1>

    @errors

    <form method="POST" action="/posts/{{ post._attributes.id }}/update" class="space-y-5">
        @csrf
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Title</label>
            <input type="text" name="title" value="@old('title', post._attributes.title)"
                   class="w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500">
            {% if errors.title %}<p class="text-red-500 text-xs mt-1">{{ errors.title[0] }}</p>{% endif %}
        </div>
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Body</label>
            <textarea name="body" rows="10"
                      class="w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500">@old('body', post._attributes.body)</textarea>
            {% if errors.body %}<p class="text-red-500 text-xs mt-1">{{ errors.body[0] }}</p>{% endif %}
        </div>
        {% if categories %}
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Category</label>
            <select name="category_id" class="w-full border border-gray-300 rounded-md px-3 py-2">
                <option value="">— None —</option>
                {% for cat in categories %}
                <option value="{{ cat._attributes.id }}" {% if cat._attributes.id == post._attributes.category_id %}selected{% endif %}>{{ cat._attributes.name }}</option>
                {% endfor %}
            </select>
        </div>
        {% endif %}
        <div class="flex items-center gap-2">
            <input type="checkbox" name="published" id="published" value="1" class="rounded"
                   {% if post._attributes.published %}checked{% endif %}>
            <label for="published" class="text-sm text-gray-700">Published</label>
        </div>
        <div class="flex gap-3">
            <button type="submit" class="bg-indigo-600 text-white px-5 py-2 rounded-md hover:bg-indigo-700">Update Post</button>
            <a href="/posts/{{ post._attributes.id }}" class="text-gray-500 px-5 py-2 hover:underline">Cancel</a>
        </div>
    </form>
</div>
@endsection
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_BLOG_ROUTES_WEB = """\
from hunt.http.router import Router


def register(router: Router) -> None:
    from app.controllers.welcome_controller import WelcomeController
    from app.controllers.post_controller import PostController

    router.get("/", WelcomeController().index).named("welcome")

    ctrl = PostController()
    router.get("/posts", ctrl.index).named("posts.index")
    router.get("/posts/create", ctrl.create).named("posts.create")
    router.post("/posts", ctrl.store).named("posts.store")
    router.get("/posts/{id}", ctrl.show).named("posts.show")
    router.get("/posts/{id}/edit", ctrl.edit).named("posts.edit")
    router.post("/posts/{id}/update", ctrl.update).named("posts.update")
    router.post("/posts/{id}/delete", ctrl.destroy).named("posts.destroy")
"""

_BLOG_ROUTES_ADMIN = """\
from hunt.admin import Admin
from hunt.admin.metrics import ValueMetric
from hunt.http.router import Router
from app.admin.user_resource import UserResource
from app.admin.post_resource import PostResource
from app.admin.category_resource import CategoryResource
from app.models.user import User
from app.models.post import Post

Admin.resource(UserResource)
Admin.resource(PostResource)
Admin.resource(CategoryResource)

Admin.dashboard(
    ValueMetric("Total Users", lambda: User.query().count()),
    ValueMetric("Total Posts", lambda: Post.query().count()),
    ValueMetric("Published Posts", lambda: Post.where("published", True).count()),
)


def register(router: Router) -> None:
    from hunt.auth.manager import Auth

    Admin.gate(
        lambda request: Auth.check()
        and bool(getattr(Auth.user(), "_attributes", {}).get("is_admin"))
    )
    Admin.register_to(router)
"""

# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

_README = """\
# Blog Starter

This application was created with the **hunt blog starter kit**.

## What was created

- **Models**: `Post`, `Category`, `Tag`
- **Controller**: `PostController` — full CRUD (index, show, create, store, edit, update, destroy)
- **Views**: `posts/index`, `posts/show`, `posts/create`, `posts/edit` (Tailwind CSS)
- **Admin**: PostResource and CategoryResource pre-wired at `/admin`
- **Migrations**: categories, posts, tags, post_tag pivot

## Get started

```bash
cd <your-app>
uv venv && uv pip install -e .
hunt migrate
hunt serve
```

Then visit:
- `http://localhost:8000/` — welcome page
- `http://localhost:8000/posts` — blog post listing
- `http://localhost:8000/register` — create an account
- `http://localhost:8000/admin` — admin panel (requires `is_admin = True` on your user)

## Promote a user to admin

After registering, open a Python shell with `hunt tinker` and run:

```python
from app.models.user import User
u = User.where("email", "you@example.com").first()
u._attributes["is_admin"] = True
u.save()
```

## Add categories via admin

Visit `/admin` → Categories → New Category to create categories before writing posts.
"""

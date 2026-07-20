"""API starter kit — versioned routes, resources, token auth stub, rate limiting."""

from __future__ import annotations

from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def apply(target: Path) -> None:
    """Overlay API starter kit files on the base skeleton."""
    _write(target / "app" / "controllers" / "api" / "__init__.py", "")
    _write(target / "app" / "controllers" / "api" / "v1" / "__init__.py", "")
    _write(target / "app" / "controllers" / "api" / "v1" / "user_controller.py", _USER_CONTROLLER)
    _write(target / "app" / "resources" / "__init__.py", "")
    _write(target / "app" / "resources" / "user_resource.py", _USER_RESOURCE)
    _write(target / "app" / "middleware" / "api_auth.py", _API_AUTH_MIDDLEWARE)
    _write(target / "app" / "middleware" / "api_rate_limit.py", _API_RATE_LIMIT_MIDDLEWARE)
    _write(target / "routes" / "api.py", _API_ROUTES)
    _write(target / "routes" / "web.py", _API_WEB_ROUTES)
    _write(target / "README.md", _README)


# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------

_USER_CONTROLLER = """\
\"\"\"GET /api/v1/users — paginated user list (requires Bearer token).\"\"\"
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response
from app.models.user import User
from app.resources.user_resource import UserResource


class UserController(Controller):
    # GET /api/v1/users
    def index(self, request: Request) -> Response:
        users = User.all()
        return self.json(UserResource.collection(users))

    # GET /api/v1/users/{id}
    def show(self, request: Request, id: int) -> Response:
        user = User.find(id)
        if user is None:
            return self.json({"error": "User not found"}, status=404)
        return self.json(UserResource(user).to_array())

    # POST /api/v1/users
    def store(self, request: Request) -> Response:
        from hunt.validation.validator import Validator
        from hunt.auth.manager import hash_password

        data = request.all()
        v = Validator.make(data, {
            "name": "required|string|max:255",
            "email": "required|email|unique:users,email",
            "password": "required|min:8",
        })
        if v.fails():
            return self.json({"errors": v.errors()._errors}, status=422)

        user = User.create({
            "name": data["name"],
            "email": data["email"],
            "password": hash_password(data["password"]),
        })
        return self.json(UserResource(user).to_array(), status=201)

    # PUT /api/v1/users/{id}
    def update(self, request: Request, id: int) -> Response:
        from hunt.validation.validator import Validator

        user = User.find(id)
        if user is None:
            return self.json({"error": "User not found"}, status=404)

        data = request.all()
        v = Validator.make(data, {"name": "string|max:255", "email": "email"})
        if v.fails():
            return self.json({"errors": v.errors()._errors}, status=422)

        for k in ("name", "email"):
            if k in data:
                user._attributes[k] = data[k]
        user.save()
        return self.json(UserResource(user).to_array())

    # DELETE /api/v1/users/{id}
    def destroy(self, request: Request, id: int) -> Response:
        user = User.find(id)
        if user is None:
            return self.json({"error": "User not found"}, status=404)
        user.delete()
        return self.json({}, status=204)
"""

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

_USER_RESOURCE = """\
\"\"\"Transform a User model into an API-safe dict.\"\"\"
from __future__ import annotations


class UserResource:
    \"\"\"Single-resource transformer — mirrors Laravel's JsonResource.\"\"\"

    def __init__(self, user) -> None:
        self._user = user

    def to_array(self) -> dict:
        attrs = self._user._attributes
        return {
            "id": attrs.get("id"),
            "name": attrs.get("name"),
            "email": attrs.get("email"),
            "created_at": str(attrs.get("created_at", "")),
        }

    @classmethod
    def collection(cls, users) -> list[dict]:
        return [cls(u).to_array() for u in users]
"""

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

_API_AUTH_MIDDLEWARE = """\
\"\"\"Bearer-token authentication for API routes.

Replace the stub token store with a real tokens table once you wire up
actual token issuance (e.g. POST /api/v1/auth/login).
\"\"\"
from hunt.auth.manager import Auth
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response


class ApiAuth(Middleware):
    async def handle(self, request: Request, next: Next) -> Response:
        auth_header = request.header("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(
                '{"error": "Unauthenticated"}',
                status=401,
                content_type="application/json",
            )

        token = auth_header.removeprefix("Bearer ").strip()
        user = self._resolve_user(token)
        if user is None:
            return Response(
                '{"error": "Invalid token"}',
                status=401,
                content_type="application/json",
            )

        Auth.login(user)
        return await next(request)

    def _resolve_user(self, token: str):
        # Implement token lookup here. Example:
        #
        #   from app.models.personal_access_token import PersonalAccessToken
        #   record = PersonalAccessToken.where("token", hash_token(token)).first()
        #   if record:
        #       from app.models.user import User
        #       return User.find(record._attributes["tokenable_id"])
        #   return None
        raise NotImplementedError(
            "ApiAuth._resolve_user() is not implemented. "
            "Open app/middleware/api_auth.py and implement token lookup."
        )
"""

_API_RATE_LIMIT_MIDDLEWARE = """\
\"\"\"Per-IP rate limiting for API routes (60 req / minute, file-backed).\"\"\"
from hunt.http.middleware.throttle import ThrottleRequests


class ApiRateLimit(ThrottleRequests):
    max_attempts = 60
    decay_seconds = 60
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_API_ROUTES = """\
\"\"\"
API routes — versioned under /api/v1.

OpenAPI hints (for documentation generators):
  GET    /api/v1/users        → list users          (requires: Bearer token)
  POST   /api/v1/users        → create user         (requires: Bearer token)
  GET    /api/v1/users/{id}   → show user            (requires: Bearer token)
  PUT    /api/v1/users/{id}   → update user          (requires: Bearer token)
  DELETE /api/v1/users/{id}   → delete user          (requires: Bearer token)
\"\"\"
from hunt.http.router import Router


def register(router: Router) -> None:
    from app.controllers.api.v1.user_controller import UserController
    from app.middleware.api_rate_limit import ApiRateLimit

    router.get("/api/health", lambda req: {"status": "ok", "version": "v1"}).named("api.health")

    ctrl = UserController()
    with router.group(prefix="/api/v1", middleware=[ApiRateLimit]):
        router.get("/users", ctrl.index).named("api.v1.users.index")
        router.post("/users", ctrl.store).named("api.v1.users.store")
        router.get("/users/{id}", ctrl.show).named("api.v1.users.show")
        router.put("/users/{id}", ctrl.update).named("api.v1.users.update")
        router.delete("/users/{id}", ctrl.destroy).named("api.v1.users.destroy")
"""

_API_WEB_ROUTES = """\
from hunt.http.router import Router


def register(router: Router) -> None:
    from app.controllers.welcome_controller import WelcomeController

    router.get("/", WelcomeController().index).named("welcome")
"""

# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

_README = """\
# API Starter

This application was created with the **hunt API starter kit**.

## What was created

- **Controllers**: `UserController` at `app/controllers/api/v1/`
- **Resources**: `UserResource` at `app/resources/` — JSON transformer
- **Middleware**: `ApiAuth` (Bearer token stub) + `ApiRateLimit` (60 req/min)
- **Routes**: Versioned under `/api/v1` with OpenAPI-style docstrings

## Get started

```bash
cd <your-app>
uv venv && uv pip install -e .
hunt migrate
hunt serve
```

## Endpoints

| Method | URI | Description |
|--------|-----|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/v1/users` | List all users |
| POST | `/api/v1/users` | Create a user |
| GET | `/api/v1/users/{id}` | Show a user |
| PUT | `/api/v1/users/{id}` | Update a user |
| DELETE | `/api/v1/users/{id}` | Delete a user |

## Example request

```bash
curl http://localhost:8000/api/v1/users \\
  -H "Authorization: Bearer your-token-here"
```

## Token authentication

Edit `app/middleware/api_auth.py` and implement `_resolve_user()` to look up
tokens from a `personal_access_tokens` table. See the TODO comment for the
exact pattern.

## Adding more resources

```bash
hunt make:api Product --fields="name:string price:decimal stock:integer"
```
"""

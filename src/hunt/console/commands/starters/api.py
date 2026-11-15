"""API starter kit — versioned routes, resources, personal access tokens, rate limiting."""

from __future__ import annotations

from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def apply(target: Path) -> None:
    """Overlay API starter kit files on the base skeleton."""
    from hunt.console.commands.new import csrf_middleware_source

    _write(target / "app" / "controllers" / "api" / "__init__.py", "")
    _write(target / "app" / "controllers" / "api" / "v1" / "__init__.py", "")
    _write(target / "app" / "controllers" / "api" / "v1" / "auth_controller.py", _AUTH_CONTROLLER)
    _write(target / "app" / "controllers" / "api" / "v1" / "user_controller.py", _USER_CONTROLLER)
    _write(target / "app" / "models" / "personal_access_token.py", _TOKEN_MODEL)
    _write(target / "app" / "resources" / "__init__.py", "")
    _write(target / "app" / "resources" / "user_resource.py", _USER_RESOURCE)
    _write(target / "app" / "middleware" / "api_auth.py", _API_AUTH_MIDDLEWARE)
    _write(target / "app" / "middleware" / "api_rate_limit.py", _API_RATE_LIMIT_MIDDLEWARE)
    # /api is exempt from CSRF only because every route below it authenticates
    # with a Bearer token (ApiAuth) and never with the session cookie.
    _write(target / "app" / "middleware" / "verify_csrf_token.py", csrf_middleware_source(["/api"]))
    _write(
        target / "database" / "migrations" / "0100_create_personal_access_tokens_table.py",
        _MIG_TOKENS,
    )
    _write(target / "routes" / "api.py", _API_ROUTES)
    _write(target / "routes" / "web.py", _API_WEB_ROUTES)
    _write(target / "README.md", _README)


# ---------------------------------------------------------------------------
# Models / migrations
# ---------------------------------------------------------------------------

_TOKEN_MODEL = """\
\"\"\"Personal access tokens for Bearer authentication.

Only the SHA-256 digest of a token is stored. The plain token is returned once,
when it is issued, and cannot be recovered afterwards.
\"\"\"
from __future__ import annotations

import hashlib
import secrets
import time

from hunt.database.model import Model

# Tokens expire after 30 days. Pass ttl=None to issue() for a non-expiring token.
DEFAULT_TTL = 60 * 60 * 24 * 30


class PersonalAccessToken(Model):
    table = "personal_access_tokens"
    # user_id, token and expires_at are set by issue(), never from request input.
    fillable = ["name"]
    hidden = ["token"]

    @staticmethod
    def hash_token(plain: str) -> str:
        return hashlib.sha256(plain.encode()).hexdigest()

    @classmethod
    def issue(cls, user_id, name: str = "api", ttl: int | None = DEFAULT_TTL) -> tuple[str, PersonalAccessToken]:
        \"\"\"Create a token for *user_id*. Returns (plain token, stored record).\"\"\"
        plain = secrets.token_urlsafe(40)
        record = cls()
        record.force_fill({
            "user_id": user_id,
            "name": str(name)[:255],
            "token": cls.hash_token(plain),
            "expires_at": int(time.time()) + int(ttl) if ttl else None,
        })
        record.save()
        return plain, record

    @classmethod
    def find_valid(cls, plain: str | None) -> PersonalAccessToken | None:
        \"\"\"Return the unexpired record for a plain token, or None.\"\"\"
        if not plain:
            return None
        record = cls.where("token", cls.hash_token(plain)).first()
        if record is None:
            return None
        expires_at = record._attributes.get("expires_at")
        if expires_at is not None and int(expires_at) < time.time():
            return None
        return record
"""

_MIG_TOKENS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreatePersonalAccessTokensTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.integer("user_id")
            table.string("name")
            # SHA-256 hex digest of the token; the plain token is never stored.
            table.string("token", 64).unique()
            table.integer("expires_at").nullable()
            table.timestamps()
            table.index("user_id")

        Schema.create("personal_access_tokens", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("personal_access_tokens")
"""

# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------

_AUTH_CONTROLLER = """\
\"\"\"Token issuing and revocation for the API.\"\"\"
from hunt.auth.manager import Auth
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response
from hunt.validation.validator import Validator
from app.models.personal_access_token import PersonalAccessToken


class AuthController(Controller):
    # POST /api/v1/auth/login   {"email": ..., "password": ..., "device_name": ...}
    def login(self, request: Request) -> Response:
        data = {
            "email": request.input("email", ""),
            "password": request.input("password", ""),
        }
        v = Validator.make(data, {"email": "required|email", "password": "required"})
        if v.fails():
            return self.json({"errors": v.errors()._errors}, status=422)

        # once() checks the password without creating a session. It also fails
        # for accounts that use two-factor authentication, so a password alone
        # can never mint a token for them.
        if not Auth.once({"email": data["email"], "password": data["password"]}):
            return self.json({"error": "These credentials do not match our records."}, status=401)

        user = Auth.user()
        name = str(request.input("device_name", "") or "api")
        plain, record = PersonalAccessToken.issue(user._attributes["id"], name)
        return self.json(
            {
                "token": plain,
                "token_type": "Bearer",
                "expires_at": record._attributes.get("expires_at"),
            },
            status=201,
        )

    # POST /api/v1/auth/logout   (revokes the token used for this request)
    def logout(self, request: Request) -> Response:
        record = getattr(request, "access_token", None)
        if record is not None:
            record.delete()
        return self.json({}, status=204)
"""

_USER_CONTROLLER = """\
\"\"\"User API. Every action requires a Bearer token (see app/middleware/api_auth.py).

Authorization:
  index, store            administrators only
  show, update, destroy   the user themself, or an administrator
\"\"\"
from hunt.auth.manager import Auth, hash_password
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response
from hunt.validation.validator import Validator
from app.models.user import User
from app.resources.user_resource import UserResource


def _is_admin(user) -> bool:
    return bool(user is not None and user._attributes.get("is_admin"))


def _find_user(id):
    try:
        return User.find(int(id))
    except (TypeError, ValueError):
        return None


class UserController(Controller):
    def _forbidden(self) -> Response:
        return self.json({"error": "Forbidden"}, status=403)

    def _not_found(self) -> Response:
        return self.json({"error": "User not found"}, status=404)

    def _can_manage(self, target) -> bool:
        \"\"\"The authenticated user may act on *target*: it is them, or they are an admin.\"\"\"
        actor = Auth.user()
        if actor is None:
            return False
        return _is_admin(actor) or str(actor._attributes.get("id")) == str(target._attributes.get("id"))

    # GET /api/v1/users
    def index(self, request: Request) -> Response:
        if not _is_admin(Auth.user()):
            return self._forbidden()
        return self.json(UserResource.collection(User.all().get()))

    # GET /api/v1/users/{id}
    def show(self, request: Request, id: int) -> Response:
        user = _find_user(id)
        if user is None:
            # Non-admins get the same answer for missing and foreign records.
            return self._not_found() if _is_admin(Auth.user()) else self._forbidden()
        if not self._can_manage(user):
            return self._forbidden()
        return self.json(UserResource(user).to_array())

    # POST /api/v1/users
    def store(self, request: Request) -> Response:
        if not _is_admin(Auth.user()):
            return self._forbidden()

        data = {key: request.input(key) for key in ("name", "email", "password")}
        v = Validator.make(data, {
            "name": "required|string|max:255",
            "email": "required|email|unique:users,email",
            "password": "required|string|min:8",
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
        user = _find_user(id)
        if user is None:
            return self._not_found() if _is_admin(Auth.user()) else self._forbidden()
        if not self._can_manage(user):
            return self._forbidden()

        # Only name and email can be changed here; is_admin and password cannot.
        data = {key: request.input(key) for key in ("name", "email") if request.has(key)}
        rules = {
            "name": "required|string|max:255",
            "email": f"required|email|unique:users,email,{user._attributes['id']}",
        }
        v = Validator.make(data, {key: rules[key] for key in data})
        if v.fails():
            return self.json({"errors": v.errors()._errors}, status=422)

        user.fill(data)
        user.save()
        return self.json(UserResource(user).to_array())

    # DELETE /api/v1/users/{id}
    def destroy(self, request: Request, id: int) -> Response:
        user = _find_user(id)
        if user is None:
            return self._not_found() if _is_admin(Auth.user()) else self._forbidden()
        if not self._can_manage(user):
            return self._forbidden()

        from app.models.personal_access_token import PersonalAccessToken

        PersonalAccessToken.where("user_id", user._attributes["id"]).delete()
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

Clients obtain a token from POST /api/v1/auth/login and send it as
`Authorization: Bearer <token>`. Tokens live in the personal_access_tokens
table as SHA-256 digests (see app/models/personal_access_token.py).
\"\"\"
from hunt.auth.manager import Auth
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response
from app.models.personal_access_token import PersonalAccessToken


def _unauthenticated(message: str) -> Response:
    response = Response('{"error": "' + message + '"}', status=401, content_type="application/json")
    response.header("WWW-Authenticate", "Bearer")
    return response


class ApiAuth(Middleware):
    async def handle(self, request: Request, next: Next) -> Response:
        token = (request.bearer_token() or "").strip()
        if not token:
            return _unauthenticated("Unauthenticated")

        user = self._resolve_user(token, request)
        if user is None:
            return _unauthenticated("Invalid token")

        return await next(request)

    def _resolve_user(self, token: str, request: Request | None = None):
        \"\"\"Return the user for a valid token, or None.

        The user is authenticated for this request only (Auth.once_using_id),
        so API calls never create a login session or session cookie.
        \"\"\"
        record = PersonalAccessToken.find_valid(token)
        if record is None:
            return None
        user = Auth.once_using_id(record._attributes["user_id"])
        if user is not None and request is not None:
            request.access_token = record
        return user
"""

_API_RATE_LIMIT_MIDDLEWARE = """\
\"\"\"Rate limits for API routes, shared across server workers (see app/middleware/throttle.py).\"\"\"
from hunt.http.request import Request
from app.middleware.throttle import Throttle


class ApiRateLimit(Throttle):
    \"\"\"60 requests per minute per client address and route.\"\"\"

    max_attempts = 60
    decay_seconds = 60


class ThrottleApiLogin(Throttle):
    \"\"\"5 token requests per minute for one account from one address.\"\"\"

    max_attempts = 5
    decay_seconds = 60

    def _resolve_key(self, request: Request) -> str:
        email = str(request.input("email", "") or "").strip().lower()
        return f"api-login:{request.ip}:{email}"
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_API_ROUTES = """\
\"\"\"
API routes — versioned under /api/v1.

OpenAPI hints (for documentation generators):
  POST   /api/v1/auth/login   → issue a token        (public, throttled)
  POST   /api/v1/auth/logout  → revoke current token (requires: Bearer token)
  GET    /api/v1/users        → list users           (requires: Bearer token, admin)
  POST   /api/v1/users        → create user          (requires: Bearer token, admin)
  GET    /api/v1/users/{id}   → show user            (requires: Bearer token, self or admin)
  PUT    /api/v1/users/{id}   → update user          (requires: Bearer token, self or admin)
  DELETE /api/v1/users/{id}   → delete user          (requires: Bearer token, self or admin)

Everything under /api is exempt from CSRF (app/middleware/verify_csrf_token.py),
so every route here except the login must stay inside the ApiAuth group: a route
that trusted the session cookie instead would be open to cross-site requests.
\"\"\"
from hunt.http.router import Router


def register(router: Router) -> None:
    from app.controllers.api.v1.auth_controller import AuthController
    from app.controllers.api.v1.user_controller import UserController
    from app.middleware.api_auth import ApiAuth
    from app.middleware.api_rate_limit import ApiRateLimit, ThrottleApiLogin

    router.get("/api/health", lambda req: {"status": "ok", "version": "v1"}).named("api.health")

    auth = AuthController()
    ctrl = UserController()
    with router.group(prefix="/api/v1", middleware=[ApiRateLimit]):
        router.post("/auth/login", auth.login).middleware(ThrottleApiLogin).named("api.v1.auth.login")

        with router.group(middleware=[ApiAuth]):
            router.post("/auth/logout", auth.logout).named("api.v1.auth.logout")
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

- **Controllers**: `AuthController` and `UserController` at `app/controllers/api/v1/`
- **Models**: `PersonalAccessToken` — hashed Bearer tokens
- **Resources**: `UserResource` at `app/resources/` — JSON transformer
- **Middleware**: `ApiAuth` (Bearer tokens) + `ApiRateLimit` (60 req/min)
- **Migration**: `personal_access_tokens`
- **Routes**: Versioned under `/api/v1` with OpenAPI-style docstrings

## Get started

```bash
cd <your-app>
uv venv && uv pip install -e .
hunt migrate
hunt serve
```

## Endpoints

| Method | URI | Auth | Description |
|--------|-----|------|-------------|
| GET | `/api/health` | none | Health check |
| POST | `/api/v1/auth/login` | none (5/min) | Exchange email + password for a token |
| POST | `/api/v1/auth/logout` | Bearer | Revoke the current token |
| GET | `/api/v1/users` | Bearer, admin | List all users |
| POST | `/api/v1/users` | Bearer, admin | Create a user |
| GET | `/api/v1/users/{id}` | Bearer, self or admin | Show a user |
| PUT | `/api/v1/users/{id}` | Bearer, self or admin | Update a user |
| DELETE | `/api/v1/users/{id}` | Bearer, self or admin | Delete a user |

## Token authentication

Register an account at `/register`, then request a token:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email": "you@example.com", "password": "your-password", "device_name": "laptop"}'
```

The response contains the token **once**; only its SHA-256 digest is stored.
Send it with every request:

```bash
curl http://localhost:8000/api/v1/users/1 \\
  -H "Authorization: Bearer <token>"
```

Tokens expire after 30 days (`DEFAULT_TTL` in `app/models/personal_access_token.py`).
Accounts with two-factor authentication cannot obtain a token with a password alone.

To make a user an administrator, run `hunt tinker`:

```python
from app.models.user import User
u = User.where("email", "you@example.com").first()
u.is_admin = True
u.save()
```

## CSRF

`/api` is exempt from CSRF protection because its routes authenticate with
Bearer tokens, never with the session cookie. Keep every new `/api` route
inside the `ApiAuth` group.

## Adding more resources

```bash
hunt make:api Product --fields="name:string price:decimal stock:integer"
```

The generated routes are protected with `ApiAuth` automatically.
"""

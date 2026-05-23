import pytest
from click.testing import CliRunner

from hunt.admin.console.make_admin_resource import make_admin_resource_command

_MODEL_SOURCE = """\
from hunt.database.model import Model


class Post(Model):
    table = "posts"
    fillable = ["title", "body"]
"""

_ROUTES_ADMIN = """\
from hunt.admin import Admin
from hunt.admin.metrics import ValueMetric
from hunt.http.router import Router
from app.admin.user_resource import UserResource
from app.models.user import User

Admin.resource(UserResource)

Admin.dashboard(
    ValueMetric("Total Users", lambda: User.query().count()),
)


def register(router: Router) -> None:
    Admin.register_to(router)
"""


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Minimal project layout with a Post model and admin routes file."""
    (tmp_path / "app" / "models").mkdir(parents=True)
    (tmp_path / "app" / "admin").mkdir(parents=True)
    (tmp_path / "routes").mkdir()

    (tmp_path / "app" / "models" / "post.py").write_text(_MODEL_SOURCE)
    (tmp_path / "routes" / "admin.py").write_text(_ROUTES_ADMIN)

    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_creates_resource_file(project):
    result = CliRunner().invoke(make_admin_resource_command, ["post"])

    assert result.exit_code == 0
    resource_file = project / "app" / "admin" / "post_resource.py"
    assert resource_file.exists()
    source = resource_file.read_text()
    assert "class PostResource(AdminResource):" in source
    assert "from app.models.post import Post" in source
    assert "model = Post" in source


def test_accepts_py_suffix(project):
    result = CliRunner().invoke(make_admin_resource_command, ["post.py"])

    assert result.exit_code == 0
    assert (project / "app" / "admin" / "post_resource.py").exists()


def test_injects_import_into_routes(project):
    CliRunner().invoke(make_admin_resource_command, ["post"])

    routes = (project / "routes" / "admin.py").read_text()
    assert "from app.admin.post_resource import PostResource" in routes


def test_injects_admin_resource_call_after_existing(project):
    CliRunner().invoke(make_admin_resource_command, ["post"])

    routes = (project / "routes" / "admin.py").read_text()
    lines = routes.splitlines()
    user_idx = next(i for i, line in enumerate(lines) if "Admin.resource(UserResource)" in line)
    post_idx = next(i for i, line in enumerate(lines) if "Admin.resource(PostResource)" in line)
    assert post_idx == user_idx + 1


def test_import_grouped_with_other_app_admin_imports(project):
    CliRunner().invoke(make_admin_resource_command, ["post"])

    routes = (project / "routes" / "admin.py").read_text()
    lines = routes.splitlines()
    user_import_idx = next(i for i, line in enumerate(lines) if "from app.admin.user_resource" in line)
    post_import_idx = next(i for i, line in enumerate(lines) if "from app.admin.post_resource" in line)
    assert post_import_idx == user_import_idx + 1


def test_prints_confirmation(project):
    result = CliRunner().invoke(make_admin_resource_command, ["post"])

    assert "AdminResource created" in result.output
    assert "routes/admin.py" in result.output


# ---------------------------------------------------------------------------
# Model file missing
# ---------------------------------------------------------------------------

def test_error_when_model_file_missing(project):
    result = CliRunner().invoke(make_admin_resource_command, ["invoice"])

    assert result.exit_code != 0
    assert "app/models/invoice.py" in result.output
    assert "hunt make:model" in result.output


def test_no_file_written_when_model_missing(project):
    CliRunner().invoke(make_admin_resource_command, ["invoice"])

    assert not (project / "app" / "admin" / "invoice_resource.py").exists()


# ---------------------------------------------------------------------------
# No class in model file
# ---------------------------------------------------------------------------

def test_error_when_no_class_in_model_file(project):
    (project / "app" / "models" / "empty.py").write_text("# nothing here\n")

    result = CliRunner().invoke(make_admin_resource_command, ["empty"])

    assert result.exit_code != 0
    assert "No class definition found" in result.output


# ---------------------------------------------------------------------------
# Already registered
# ---------------------------------------------------------------------------

def test_error_when_already_registered(project):
    CliRunner().invoke(make_admin_resource_command, ["post"])
    # Re-create the resource file so the file-exists check doesn't fire first
    (project / "app" / "admin" / "post_resource.py").unlink()

    result = CliRunner().invoke(make_admin_resource_command, ["post"])

    assert result.exit_code != 0
    assert "already registered" in result.output


# ---------------------------------------------------------------------------
# Resource file already exists
# ---------------------------------------------------------------------------

def test_error_when_resource_file_exists(project):
    (project / "app" / "admin" / "post_resource.py").write_text("# placeholder\n")

    result = CliRunner().invoke(make_admin_resource_command, ["post"])

    assert result.exit_code != 0
    assert "already exists" in result.output


def test_routes_not_modified_when_resource_file_exists(project):
    (project / "app" / "admin" / "post_resource.py").write_text("# placeholder\n")
    original = (project / "routes" / "admin.py").read_text()

    CliRunner().invoke(make_admin_resource_command, ["post"])

    assert (project / "routes" / "admin.py").read_text() == original


# ---------------------------------------------------------------------------
# No routes/admin.py present
# ---------------------------------------------------------------------------

def test_succeeds_without_routes_file(project):
    (project / "routes" / "admin.py").unlink()

    result = CliRunner().invoke(make_admin_resource_command, ["post"])

    assert result.exit_code == 0
    assert (project / "app" / "admin" / "post_resource.py").exists()


# ---------------------------------------------------------------------------
# Class name derived from file, not argument
# ---------------------------------------------------------------------------

def test_class_name_read_from_file_not_argument(project):
    # File is named 'blog_post.py' but the class inside is 'Entry'
    (project / "app" / "models" / "blog_post.py").write_text(
        "from hunt.database.model import Model\n\nclass Entry(Model):\n    table = 'entries'\n"
    )

    result = CliRunner().invoke(make_admin_resource_command, ["blog_post"])

    assert result.exit_code == 0
    source = (project / "app" / "admin" / "entry_resource.py").read_text()
    assert "class EntryResource(AdminResource):" in source
    assert "from app.models.blog_post import Entry" in source

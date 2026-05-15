"""Tests for the route:list command."""
import pytest
from click.testing import CliRunner
from unittest.mock import MagicMock, patch

from hunt.console.commands.route_list import route_list_command


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_error_when_bootstrap_cannot_be_loaded(project):
    result = CliRunner().invoke(route_list_command, [])

    assert result.exit_code == 0
    assert "Could not load routes" in result.output


def test_empty_message_when_no_routes(project):
    mock_router = MagicMock()
    mock_router.routes.return_value = []
    mock_app = MagicMock()
    mock_app.make.return_value = mock_router

    with patch.dict("sys.modules", {"bootstrap.app": MagicMock(application=mock_app)}):
        result = CliRunner().invoke(route_list_command, [])

    assert result.exit_code == 0
    assert "No routes registered" in result.output


def test_lists_routes(project):
    mock_route = MagicMock()
    mock_route.methods = ["GET"]
    mock_route.uri = "/posts"
    mock_route.name = "posts.index"
    mock_route.middleware = []

    mock_router = MagicMock()
    mock_router.routes.return_value = [mock_route]
    mock_app = MagicMock()
    mock_app.make.return_value = mock_router

    with patch.dict("sys.modules", {"bootstrap.app": MagicMock(application=mock_app)}):
        result = CliRunner().invoke(route_list_command, [])

    assert result.exit_code == 0
    assert "/posts" in result.output
    assert "posts.index" in result.output

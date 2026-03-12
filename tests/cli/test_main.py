from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from axon import __version__
from axon.cli.main import _register_in_global_registry, app

runner = CliRunner()


@pytest.fixture(autouse=True)
def suppress_update_notice(request):
    if request.node.get_closest_marker("allow_update_notice"):
        yield
        return
    with patch("axon.cli.main._maybe_notify_update"):
        yield


class TestVersion:
    def test_version_long_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0


class TestUpdateNotifier:
    @pytest.mark.allow_update_notice
    def test_shows_update_notice_for_normal_command(self) -> None:
        with patch("axon.cli.main._get_latest_version", return_value="9.9.9"):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "Update available" in result.output

    def test_version_short_flag(self) -> None:
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0
        assert f"Axon v{__version__}" in result.output

    def test_version_exit_code(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0


class TestHelp:
    def test_help_exit_code(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_help_shows_app_name(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Axon" in result.output

    def test_help_lists_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        expected_commands = [
            "analyze",
            "status",
            "list",
            "clean",
            "query",
            "context",
            "impact",
            "dead-code",
            "cypher",
            "watch",
            "diff",
            "ui",
        ]
        for cmd in expected_commands:
            assert cmd in result.output, f"Command '{cmd}' not found in --help output"
        # Removed commands must not appear
        for removed in ("setup", "mcp", "serve", "host"):
            assert removed not in result.output, f"Removed command '{removed}' still in --help"


class TestStatus:
    def test_status_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        from axon.core.repo_context import RepoContext

        fake_ctx = RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=tmp_path.name,
            storage_dir=tmp_path / "storage",
            db_path=tmp_path / "storage" / "kuzu",
            meta_path=tmp_path / "storage" / "meta.json",
            is_worktree=False,
            worktree_branch=None,
        )
        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_status_with_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        from axon.core.repo_context import RepoContext

        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        meta_path = storage_dir / "meta.json"
        meta = {
            "version": "0.1.0",
            "stats": {
                "files": 10,
                "symbols": 42,
                "relationships": 100,
                "clusters": 3,
                "flows": 0,
                "dead_code": 5,
                "coupled_pairs": 0,
            },
            "last_indexed_at": "2025-01-15T10:00:00+00:00",
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        fake_ctx = RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=tmp_path.name,
            storage_dir=storage_dir,
            db_path=storage_dir / "kuzu",
            meta_path=meta_path,
            is_worktree=False,
            worktree_branch=None,
        )
        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Index status for" in result.output
        assert "0.1.0" in result.output
        assert "10" in result.output  # files
        assert "42" in result.output  # symbols
        assert "100" in result.output  # relationships


class TestListRepos:
    def test_list_calls_handle_list_repos(self) -> None:
        with patch(
            "axon.mcp.tools.handle_list_repos",
            return_value="Indexed repositories (1):\n\n  1. my-project",
        ):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "my-project" in result.output

    def test_list_no_repos(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        with patch(
            "axon.mcp.tools.handle_list_repos",
            return_value="No indexed repositories found.",
        ):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No indexed repositories found" in result.output


class TestClean:
    def test_clean_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        from axon.core.repo_context import RepoContext

        fake_ctx = RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=tmp_path.name,
            storage_dir=tmp_path / "storage",
            db_path=tmp_path / "storage" / "kuzu",
            meta_path=tmp_path / "storage" / "meta.json",
            is_worktree=False,
            worktree_branch=None,
        )
        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            result = runner.invoke(app, ["clean", "--force"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_clean_with_force(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        from axon.core.repo_context import RepoContext

        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        (storage_dir / "meta.json").write_text("{}", encoding="utf-8")

        fake_ctx = RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=tmp_path.name,
            storage_dir=storage_dir,
            db_path=storage_dir / "kuzu",
            meta_path=storage_dir / "meta.json",
            is_worktree=False,
            worktree_branch=None,
        )
        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            result = runner.invoke(app, ["clean", "--force"])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        assert not storage_dir.exists()

    def test_clean_aborted(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        from axon.core.repo_context import RepoContext

        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        (storage_dir / "meta.json").write_text("{}", encoding="utf-8")

        fake_ctx = RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=tmp_path.name,
            storage_dir=storage_dir,
            db_path=storage_dir / "kuzu",
            meta_path=storage_dir / "meta.json",
            is_worktree=False,
            worktree_branch=None,
        )
        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            result = runner.invoke(app, ["clean"], input="n\n")
        assert result.exit_code == 0
        assert storage_dir.exists()  # Not deleted


class TestQuery:
    def test_query_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["query", "find classes"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_query_with_storage(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        mock_storage = MagicMock()
        with patch("axon.cli.main._load_storage", return_value=mock_storage):
            with patch(
                "axon.mcp.tools.handle_query",
                return_value="1. MyClass (Class) -- src/main.py",
            ):
                result = runner.invoke(app, ["query", "find classes"])
        assert result.exit_code == 0
        assert "MyClass" in result.output


class TestContext:
    def test_context_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["context", "MyClass"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_context_with_storage(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        mock_storage = MagicMock()
        with patch("axon.cli.main._load_storage", return_value=mock_storage):
            with patch(
                "axon.mcp.tools.handle_context",
                return_value="Symbol: MyClass (Class)\nFile: src/main.py:1-50",
            ):
                result = runner.invoke(app, ["context", "MyClass"])
        assert result.exit_code == 0
        assert "MyClass" in result.output


class TestImpact:
    def test_impact_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["impact", "MyClass.method"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_impact_with_storage(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        mock_storage = MagicMock()
        with patch("axon.cli.main._load_storage", return_value=mock_storage):
            with patch(
                "axon.mcp.tools.handle_impact",
                return_value="Impact analysis for: MyClass.method",
            ):
                result = runner.invoke(app, ["impact", "MyClass.method", "--depth", "5"])
        assert result.exit_code == 0
        assert "Impact analysis" in result.output

    def test_impact_default_depth(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        mock_storage = MagicMock()
        with patch("axon.cli.main._load_storage", return_value=mock_storage):
            with patch(
                "axon.mcp.tools.handle_impact",
                return_value="Impact analysis for: foo",
            ):
                result = runner.invoke(app, ["impact", "foo"])
        assert result.exit_code == 0


class TestDeadCode:
    def test_dead_code_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["dead-code"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_dead_code_with_storage(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock_storage = MagicMock()
        with patch("axon.cli.main._load_storage", return_value=mock_storage):
            with patch(
                "axon.mcp.tools.handle_dead_code",
                return_value="No dead code detected.",
            ):
                result = runner.invoke(app, ["dead-code"])
        assert result.exit_code == 0
        assert "No dead code detected" in result.output


class TestCypher:
    def test_cypher_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["cypher", "MATCH (n) RETURN n"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_cypher_with_storage(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        mock_storage = MagicMock()
        with patch("axon.cli.main._load_storage", return_value=mock_storage):
            with patch(
                "axon.mcp.tools.handle_cypher",
                return_value="Results (3 rows):\n\n  1. foo",
            ):
                result = runner.invoke(app, ["cypher", "MATCH (n) RETURN n"])
        assert result.exit_code == 0
        assert "Results" in result.output


class TestUi:
    def test_ui_no_index(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        # No index — resolve_repo_context returns a ctx with non-existent db_path
        result = runner.invoke(app, ["ui", "--no-open"])
        assert result.exit_code == 1
        assert "No index found" in result.output

    def test_ui_launches_web_app(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        monkeypatch.chdir(tmp_path)
        # Simulate an indexed repo by making db_path exist
        from axon.core.repo_context import RepoContext

        fake_ctx = RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=tmp_path.name,
            storage_dir=tmp_path / ".axon_test",
            db_path=tmp_path / ".axon_test" / "kuzu",
            meta_path=tmp_path / ".axon_test" / "meta.json",
            is_worktree=False,
            worktree_branch=None,
        )
        fake_ctx.db_path.mkdir(parents=True)
        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            with patch("axon.web.app.create_app") as mock_create_app:
                with patch("uvicorn.run") as mock_run:
                    result = runner.invoke(app, ["ui", "--no-open"])
        assert result.exit_code == 0
        mock_create_app.assert_called_once()
        mock_run.assert_called_once()


class TestWatch:
    def test_watch_command_exists(self) -> None:
        result = runner.invoke(app, ["watch", "--help"])
        assert result.exit_code == 0
        assert "Watch mode" in result.output or "re-index" in result.output.lower()

    def test_diff_command_exists(self) -> None:
        result = runner.invoke(app, ["diff", "--help"])
        assert result.exit_code == 0
        assert "branch" in result.output.lower()


# Multi-repo registry


class TestRegisterInGlobalRegistry:
    def _make_fake_ctx(self, tmp_path: Path, slug: str | None = None):
        from axon.core.repo_context import RepoContext

        effective_slug = slug or tmp_path.name
        storage_dir = tmp_path / "storage" / effective_slug
        return RepoContext(
            main_root=tmp_path,
            current_path=tmp_path,
            repo_name=tmp_path.name,
            slug=effective_slug,
            storage_dir=storage_dir,
            db_path=storage_dir / "kuzu",
            meta_path=storage_dir / "meta.json",
            is_worktree=False,
            worktree_branch=None,
        )

    def test_writes_meta_with_slug(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "my-project"
        repo_path.mkdir()
        meta = {"name": "my-project", "path": str(repo_path), "stats": {}}

        fake_ctx = self._make_fake_ctx(tmp_path, slug="my-project")

        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            _register_in_global_registry(meta, repo_path)

        assert fake_ctx.meta_path.exists()
        written = json.loads(fake_ctx.meta_path.read_text())
        assert written["name"] == "my-project"
        assert written["slug"] == "my-project"
        assert written["path"] == str(repo_path)

    def test_storage_dir_created(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "my-project"
        repo_path.mkdir()
        meta = {"name": "my-project", "path": str(repo_path), "stats": {}}

        fake_ctx = self._make_fake_ctx(tmp_path, slug="my-project")
        assert not fake_ctx.storage_dir.exists()

        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            _register_in_global_registry(meta, repo_path)

        assert fake_ctx.storage_dir.exists()

    def test_slug_from_ctx_used(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "myapp"
        repo_path.mkdir()
        meta = {"name": "myapp", "path": str(repo_path), "stats": {}}

        # Simulate a collision slug assigned by resolve_repo_context
        fake_ctx = self._make_fake_ctx(tmp_path, slug="myapp-ab12cd34")

        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            _register_in_global_registry(meta, repo_path)

        written = json.loads(fake_ctx.meta_path.read_text())
        assert written["slug"] == "myapp-ab12cd34"

    def test_overwrites_existing_meta(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "my-project"
        repo_path.mkdir()

        fake_ctx = self._make_fake_ctx(tmp_path, slug="my-project")
        fake_ctx.storage_dir.mkdir(parents=True)
        fake_ctx.meta_path.write_text(json.dumps({"old": "data"}), encoding="utf-8")

        meta = {"name": "my-project", "path": str(repo_path), "stats": {"files": 5}}

        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            _register_in_global_registry(meta, repo_path)

        written = json.loads(fake_ctx.meta_path.read_text())
        assert written["stats"] == {"files": 5}
        assert "old" not in written

    def test_does_not_mutate_input_meta(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "my-project"
        repo_path.mkdir()
        meta = {"name": "my-project", "path": str(repo_path)}

        fake_ctx = self._make_fake_ctx(tmp_path, slug="my-project")

        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx):
            _register_in_global_registry(meta, repo_path)

        # Original dict must not have been modified
        assert "slug" not in meta

    def test_resolve_repo_context_called_with_repo_path(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "my-project"
        repo_path.mkdir()
        meta = {"name": "my-project", "path": str(repo_path)}

        fake_ctx = self._make_fake_ctx(tmp_path, slug="my-project")

        with patch("axon.cli.main.resolve_repo_context", return_value=fake_ctx) as mock_resolve:
            _register_in_global_registry(meta, repo_path)

        mock_resolve.assert_called_once_with(repo_path)

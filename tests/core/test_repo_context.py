from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from axon.core.repo_context import (
    RepoContext,
    _find_git_root,
    _resolve_slug,
    resolve_repo_context,
)


# ---------------------------------------------------------------------------
# _find_git_root
# ---------------------------------------------------------------------------


class TestFindGitRootNormalRepo:
    def test_git_dir_at_root_returns_current(self, tmp_path: Path) -> None:
        """When .git is a directory, returns (current, False, None)."""
        (tmp_path / ".git").mkdir()
        main_root, is_worktree, branch = _find_git_root(tmp_path)
        assert main_root == tmp_path.resolve()
        assert is_worktree is False
        assert branch is None

    def test_nested_dir_walks_up_to_git(self, tmp_path: Path) -> None:
        """.git exists in a parent — walks up and finds it."""
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "src" / "utils"
        nested.mkdir(parents=True)
        main_root, is_worktree, branch = _find_git_root(nested)
        assert main_root == tmp_path.resolve()
        assert is_worktree is False
        assert branch is None

    def test_returns_resolved_path(self, tmp_path: Path) -> None:
        """Returned main_root is an absolute resolved path."""
        (tmp_path / ".git").mkdir()
        main_root, _, _ = _find_git_root(tmp_path)
        assert main_root.is_absolute()


class TestFindGitRootWorktree:
    def test_git_file_worktree_returns_main_root(self, tmp_path: Path) -> None:
        """When .git is a file with gitdir pointing to a worktree, returns (main_root, True, branch)."""
        # Simulate main repo at tmp_path/main
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        main_git = main_repo / ".git"
        main_git.mkdir()
        worktrees_dir = main_git / "worktrees" / "feature-branch"
        worktrees_dir.mkdir(parents=True)

        # Simulate worktree at tmp_path/worktree
        worktree_dir = tmp_path / "worktree"
        worktree_dir.mkdir()
        gitdir_path = worktrees_dir
        (worktree_dir / ".git").write_text(f"gitdir: {gitdir_path}\n", encoding="utf-8")

        main_root, is_worktree, branch = _find_git_root(worktree_dir)
        assert is_worktree is True
        assert branch == "feature-branch"
        assert main_root == main_repo.resolve()

    def test_worktree_branch_extracted_from_path(self, tmp_path: Path) -> None:
        """Branch name matches the final component of the worktree gitdir path."""
        main_repo = tmp_path / "repo"
        main_repo.mkdir()
        (main_repo / ".git").mkdir()
        wt_gitdir = main_repo / ".git" / "worktrees" / "my-feature"
        wt_gitdir.mkdir(parents=True)

        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

        _, _, branch = _find_git_root(worktree)
        assert branch == "my-feature"


class TestFindGitRootNoGit:
    def test_no_git_returns_start_path(self, tmp_path: Path) -> None:
        """When no .git is found anywhere, returns (start_path, False, None)."""
        # tmp_path has no .git; use an isolated subtree so we don't accidentally
        # pick up a real .git by walking up past tmp_path
        isolated = tmp_path / "project"
        isolated.mkdir()

        with patch("axon.core.repo_context._find_git_root") as _mock:
            # Call the real implementation but patch Path.parent traversal stopping
            pass  # just use the function directly with a path unlikely to have .git above it

        # We test the behaviour by pointing at a deeply nested dir inside tmp_path.
        # pytest's tmp_path is under /tmp (or platform equivalent) which itself has no .git.
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        main_root, is_worktree, branch = _find_git_root(deep)
        assert is_worktree is False
        assert branch is None
        # main_root should be the start_path resolved
        assert main_root == deep.resolve()


class TestFindGitRootMalformedGitFile:
    def test_malformed_git_file_treated_as_normal(self, tmp_path: Path) -> None:
        """.git file without 'gitdir:' prefix → treated as non-worktree."""
        (tmp_path / ".git").write_text("not-a-gitdir-line\n", encoding="utf-8")
        main_root, is_worktree, branch = _find_git_root(tmp_path)
        assert main_root == tmp_path.resolve()
        assert is_worktree is False
        assert branch is None

    def test_empty_git_file_treated_as_normal(self, tmp_path: Path) -> None:
        """Empty .git file falls back to non-worktree."""
        (tmp_path / ".git").write_text("", encoding="utf-8")
        main_root, is_worktree, branch = _find_git_root(tmp_path)
        assert is_worktree is False
        assert branch is None


# ---------------------------------------------------------------------------
# _resolve_slug
# ---------------------------------------------------------------------------


class TestResolveSlugFirstRegistration:
    def test_no_registry_returns_repo_name(self, tmp_path: Path) -> None:
        """When the registry directory does not exist at all, return plain repo_name."""
        registry_root = tmp_path / "repos"  # does not exist yet
        slug = _resolve_slug("myrepo", tmp_path / "myrepo", registry_root)
        assert slug == "myrepo"

    def test_empty_registry_returns_repo_name(self, tmp_path: Path) -> None:
        """An empty registry directory produces the plain repo_name."""
        registry_root = tmp_path / "repos"
        registry_root.mkdir()
        slug = _resolve_slug("myrepo", tmp_path / "myrepo", registry_root)
        assert slug == "myrepo"


class TestResolveSlugReRegistration:
    def test_same_repo_reregistered_returns_existing_slug(self, tmp_path: Path) -> None:
        """If a slug already exists for this main_root, return that slug unchanged."""
        registry_root = tmp_path / "repos"
        main_root = tmp_path / "myrepo"
        existing_slug = "myrepo"
        slot = registry_root / existing_slug
        slot.mkdir(parents=True)
        (slot / "meta.json").write_text(json.dumps({"path": str(main_root)}), encoding="utf-8")

        slug = _resolve_slug("myrepo", main_root, registry_root)
        assert slug == existing_slug

    def test_existing_slug_different_name_still_returned(self, tmp_path: Path) -> None:
        """If repo was previously stored under a different slug name, that slug is reused."""
        registry_root = tmp_path / "repos"
        main_root = tmp_path / "myrepo"
        # Imagine repo was stored under a hash slug previously
        old_slug = "myrepo-abc12345"
        slot = registry_root / old_slug
        slot.mkdir(parents=True)
        (slot / "meta.json").write_text(json.dumps({"path": str(main_root)}), encoding="utf-8")

        slug = _resolve_slug("myrepo", main_root, registry_root)
        assert slug == old_slug


class TestResolveSlugCollision:
    def test_name_collision_with_different_repo_adds_hash(self, tmp_path: Path) -> None:
        """Different repo already occupying repo_name slot → appends 8-char hash."""
        registry_root = tmp_path / "repos"
        other_root = tmp_path / "other-myrepo"
        new_root = tmp_path / "my-myrepo"

        # Register a *different* repo under "myrepo"
        slot = registry_root / "myrepo"
        slot.mkdir(parents=True)
        (slot / "meta.json").write_text(json.dumps({"path": str(other_root)}), encoding="utf-8")

        slug = _resolve_slug("myrepo", new_root, registry_root)

        expected_hash = hashlib.sha256(str(new_root).encode()).hexdigest()[:8]
        assert slug == f"myrepo-{expected_hash}"

    def test_no_collision_when_same_repo_in_slot(self, tmp_path: Path) -> None:
        """Slot exists but is already for the same repo → return plain repo_name."""
        registry_root = tmp_path / "repos"
        main_root = tmp_path / "myrepo"

        slot = registry_root / "myrepo"
        slot.mkdir(parents=True)
        (slot / "meta.json").write_text(json.dumps({"path": str(main_root)}), encoding="utf-8")

        slug = _resolve_slug("myrepo", main_root, registry_root)
        assert slug == "myrepo"

    def test_collision_slug_is_stable(self, tmp_path: Path) -> None:
        """Hash suffix is deterministic — two calls produce the same slug."""
        registry_root = tmp_path / "repos"
        other_root = tmp_path / "other"
        new_root = tmp_path / "new"

        slot = registry_root / "project"
        slot.mkdir(parents=True)
        (slot / "meta.json").write_text(json.dumps({"path": str(other_root)}), encoding="utf-8")

        slug1 = _resolve_slug("project", new_root, registry_root)
        slug2 = _resolve_slug("project", new_root, registry_root)
        assert slug1 == slug2


# ---------------------------------------------------------------------------
# resolve_repo_context
# ---------------------------------------------------------------------------


@pytest.fixture()
def axon_home(tmp_path: Path) -> Path:
    """Returns a temporary ~/.axon replacement and patches get_axon_home()."""
    fake_home = tmp_path / ".axon"
    fake_home.mkdir()
    return fake_home


class TestResolveRepoContextNormalRepo:
    def test_normal_repo_is_not_worktree(self, tmp_path: Path, axon_home: Path) -> None:
        """A repo with a .git directory is not a worktree."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.is_worktree is False
        assert ctx.worktree_branch is None

    def test_normal_repo_main_root_equals_current_path(
        self, tmp_path: Path, axon_home: Path
    ) -> None:
        """For normal repos, main_root == current_path."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.main_root == ctx.current_path

    def test_normal_repo_name_from_directory(self, tmp_path: Path, axon_home: Path) -> None:
        """repo_name is derived from the main_root directory name."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.repo_name == "myproject"

    def test_normal_repo_storage_paths_under_axon_home(
        self, tmp_path: Path, axon_home: Path
    ) -> None:
        """storage_dir, db_path, and meta_path live under the axon home repos dir."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.storage_dir.is_relative_to(axon_home / "repos")
        assert ctx.db_path == ctx.storage_dir / "kuzu"
        assert ctx.meta_path == ctx.storage_dir / "meta.json"

    def test_normal_repo_slug_matches_repo_name(self, tmp_path: Path, axon_home: Path) -> None:
        """First registration: slug == repo_name (no hash suffix)."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.slug == "myproject"

    def test_returns_repo_context_dataclass(self, tmp_path: Path, axon_home: Path) -> None:
        """resolve_repo_context returns a RepoContext instance."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert isinstance(ctx, RepoContext)


class TestResolveRepoContextWorktree:
    def _make_worktree(self, tmp_path: Path, branch: str = "feature-x") -> tuple[Path, Path]:
        """Build a main repo and a linked worktree under tmp_path.

        Returns (main_repo_path, worktree_path).
        """
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        main_git = main_repo / ".git"
        main_git.mkdir()
        wt_gitdir = main_git / "worktrees" / branch
        wt_gitdir.mkdir(parents=True)

        worktree = tmp_path / "worktrees" / branch
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
        return main_repo, worktree

    def test_worktree_is_worktree_true(self, tmp_path: Path, axon_home: Path) -> None:
        """is_worktree is True when resolved from inside a git worktree."""
        main_repo, worktree = self._make_worktree(tmp_path)

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(worktree)

        assert ctx.is_worktree is True

    def test_worktree_branch_set(self, tmp_path: Path, axon_home: Path) -> None:
        """worktree_branch matches the branch name embedded in the gitdir path."""
        main_repo, worktree = self._make_worktree(tmp_path, branch="my-branch")

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(worktree)

        assert ctx.worktree_branch == "my-branch"

    def test_worktree_main_root_points_to_main_repo(self, tmp_path: Path, axon_home: Path) -> None:
        """main_root resolves to the main repository, not the worktree directory."""
        main_repo, worktree = self._make_worktree(tmp_path)

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(worktree)

        assert ctx.main_root == main_repo.resolve()

    def test_worktree_current_path_is_worktree_dir(self, tmp_path: Path, axon_home: Path) -> None:
        """current_path reflects the actual worktree directory, not the main repo."""
        main_repo, worktree = self._make_worktree(tmp_path)

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(worktree)

        assert ctx.current_path == worktree.resolve()
        assert ctx.current_path != ctx.main_root

    def test_worktree_storage_paths_are_correct(self, tmp_path: Path, axon_home: Path) -> None:
        """db_path and meta_path are correctly nested inside storage_dir."""
        main_repo, worktree = self._make_worktree(tmp_path)

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(worktree)

        assert ctx.db_path == ctx.storage_dir / "kuzu"
        assert ctx.meta_path == ctx.storage_dir / "meta.json"


class TestResolveRepoContextPaths:
    def test_db_path_named_kuzu(self, tmp_path: Path, axon_home: Path) -> None:
        """db_path always ends in 'kuzu'."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.db_path.name == "kuzu"

    def test_meta_path_named_meta_json(self, tmp_path: Path, axon_home: Path) -> None:
        """meta_path always ends in 'meta.json'."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.meta_path.name == "meta.json"

    def test_storage_dir_is_repos_slash_slug(self, tmp_path: Path, axon_home: Path) -> None:
        """storage_dir is exactly axon_home/repos/{slug}."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
            ctx = resolve_repo_context(repo)

        assert ctx.storage_dir == axon_home / "repos" / ctx.slug

    def test_default_path_uses_cwd(self, tmp_path: Path, axon_home: Path) -> None:
        """When path=None, current working directory is used."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()

        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            with patch("axon.core.repo_context.get_axon_home", return_value=axon_home):
                ctx = resolve_repo_context()  # no path argument
        finally:
            os.chdir(original_cwd)

        assert ctx.current_path == repo.resolve()

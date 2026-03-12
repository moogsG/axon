"""Centralized repository context and git worktree detection.

This module provides the core RepoContext dataclass and resolution logic for
managing Axon's per-repository storage, with full support for git worktrees.

The key function is `resolve_repo_context()`, which:
1. Detects git worktrees by examining .git files vs directories
2. Resolves the main repository root (even from within a worktree)
3. Generates stable storage slugs with collision detection
4. Provides consistent paths for database and metadata storage

Example:
    >>> ctx = resolve_repo_context()
    >>> print(ctx.repo_name)
    'axon'
    >>> print(ctx.is_worktree)
    False
    >>> print(ctx.db_path)
    PosixPath('/Users/morgan/.axon/repos/axon/kuzu')
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RepoContext:
    """Complete context for a repository's Axon storage and git configuration.

    Attributes:
        main_root: The root directory of the main git repository (not the worktree).
                   For normal repos, this equals current_path. For worktrees, this
                   points to the original repository root.
        current_path: The actual directory we're operating in. May be a worktree path.
        repo_name: The name of the repository (derived from main_root directory name).
        slug: Unique identifier for storage. Usually repo_name, but may include a
              hash suffix if multiple repos share the same name.
        storage_dir: Root directory for all Axon data for this repository.
        db_path: Path to the Kuzu database directory.
        meta_path: Path to the metadata JSON file.
        is_worktree: True if current_path is inside a git worktree.
        worktree_branch: The branch name of the worktree, or None if not a worktree.
    """

    main_root: Path
    current_path: Path
    repo_name: str
    slug: str
    storage_dir: Path
    db_path: Path
    meta_path: Path
    is_worktree: bool
    worktree_branch: str | None


def get_axon_home() -> Path:
    """Returns the Axon home directory (~/.axon), creating it if needed.

    Returns:
        Path to ~/.axon directory.
    """
    home = Path.home() / ".axon"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _find_git_root(start_path: Path) -> tuple[Path, bool, str | None]:
    """Walk up from start_path to find .git and detect worktree status.

    Args:
        start_path: Directory to start searching from.

    Returns:
        Tuple of (main_root, is_worktree, worktree_branch):
        - main_root: The root of the main repository
        - is_worktree: True if this is a git worktree
        - worktree_branch: Branch name if worktree, else None

    Notes:
        - If .git is a directory: normal repo, main_root = current directory
        - If .git is a file: worktree, parse gitdir to find main repo
        - If no .git found: treat start_path as root, not a worktree
    """
    current = start_path.resolve()

    # Walk up the directory tree looking for .git
    while True:
        git_path = current / ".git"

        if git_path.exists():
            if git_path.is_dir():
                # Normal repository
                return current, False, None
            elif git_path.is_file():
                # Git worktree - .git is a file containing gitdir reference
                try:
                    gitdir_content = git_path.read_text().strip()

                    # Expected format: "gitdir: /path/to/main/repo/.git/worktrees/branch-name"
                    if gitdir_content.startswith("gitdir: "):
                        gitdir_path = Path(gitdir_content[8:])  # Strip "gitdir: " prefix

                        # The main .git directory is two levels up from the worktree gitdir
                        # e.g., /repo/.git/worktrees/branch -> /repo/.git
                        main_git_dir = gitdir_path.parent.parent
                        main_root = main_git_dir.parent

                        # Extract branch name from the worktree path
                        worktree_branch = gitdir_path.name

                        return main_root, True, worktree_branch
                    else:
                        # Malformed .git file - treat as non-worktree
                        return current, False, None

                except (OSError, IndexError):
                    # Error reading .git file - fall back to treating as normal repo
                    return current, False, None

        # Move up one directory
        parent = current.parent
        if parent == current:
            # Reached filesystem root without finding .git
            # Treat the start path as the root
            return start_path.resolve(), False, None

        current = parent


def _load_meta_json(meta_path: Path) -> dict[str, Any] | None:
    """Load and parse a meta.json file.

    Args:
        meta_path: Path to the meta.json file.

    Returns:
        Parsed JSON as dict, or None if file doesn't exist or is invalid.
    """
    try:
        if meta_path.exists():
            return json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _find_existing_slug(registry_root: Path, main_root: Path) -> str | None:
    """Scan the registry for an existing slug matching this main_root.

    Args:
        registry_root: The ~/.axon/repos directory.
        main_root: The main repository root to search for.

    Returns:
        The existing slug if found, otherwise None.
    """
    if not registry_root.exists():
        return None

    main_root_str = str(main_root)

    try:
        for candidate_dir in registry_root.iterdir():
            if not candidate_dir.is_dir():
                continue

            meta_path = candidate_dir / "meta.json"
            meta = _load_meta_json(meta_path)

            if meta and meta.get("path") == main_root_str:
                return candidate_dir.name
    except OSError:
        pass

    return None


def _resolve_slug(repo_name: str, main_root: Path, registry_root: Path) -> str:
    """Resolve a unique slug for this repository, handling collisions.

    Args:
        repo_name: The base repository name.
        main_root: The main repository root path.
        registry_root: The ~/.axon/repos directory.

    Returns:
        A unique slug, either repo_name or repo_name-{hash} if collision detected.

    Notes:
        First checks if this repo already has a registered slug (handles renames).
        Then checks for name collisions with other repos.
    """
    # Step 1: Check if this repo already has a registered slug
    existing_slug = _find_existing_slug(registry_root, main_root)
    if existing_slug:
        return existing_slug

    # Step 2: Check for collision with the base repo_name
    candidate_slug = repo_name
    candidate_dir = registry_root / candidate_slug

    if candidate_dir.exists():
        # Directory exists - check if it's for a different repository
        meta = _load_meta_json(candidate_dir / "meta.json")

        if meta and meta.get("path") != str(main_root):
            # Collision detected - different repo with same name
            # Generate a unique suffix using a hash of the path
            path_hash = hashlib.sha256(str(main_root).encode()).hexdigest()[:8]
            candidate_slug = f"{repo_name}-{path_hash}"

    return candidate_slug


def resolve_repo_context(path: Path | None = None) -> RepoContext:
    """Resolve complete repository context with git worktree detection.

    This is the main entry point for obtaining repository context. It handles:
    - Git worktree detection and main repository resolution
    - Stable slug generation with collision detection
    - Consistent storage path derivation

    Args:
        path: Starting path for resolution. Defaults to current working directory.

    Returns:
        Complete RepoContext with all paths and metadata resolved.

    Example:
        >>> # From within a normal repository
        >>> ctx = resolve_repo_context()
        >>> ctx.is_worktree
        False
        >>> ctx.main_root == ctx.current_path
        True

        >>> # From within a git worktree
        >>> ctx = resolve_repo_context(Path("/workspace/repo/trees/feature-branch"))
        >>> ctx.is_worktree
        True
        >>> ctx.worktree_branch
        'feature-branch'
        >>> ctx.main_root
        PosixPath('/workspace/repo')
    """
    if path is None:
        path = Path.cwd()

    current_path = path.resolve()

    # Step 1: Find git root and detect worktree
    main_root, is_worktree, worktree_branch = _find_git_root(current_path)

    # Step 2: Derive repo name from main root
    repo_name = main_root.name

    # Step 3: Resolve slug with collision detection
    registry_root = get_axon_home() / "repos"
    slug = _resolve_slug(repo_name, main_root, registry_root)

    # Step 4: Build storage paths
    storage_dir = registry_root / slug
    db_path = storage_dir / "kuzu"
    meta_path = storage_dir / "meta.json"

    # Step 5: Return complete context
    return RepoContext(
        main_root=main_root,
        current_path=current_path,
        repo_name=repo_name,
        slug=slug,
        storage_dir=storage_dir,
        db_path=db_path,
        meta_path=meta_path,
        is_worktree=is_worktree,
        worktree_branch=worktree_branch,
    )

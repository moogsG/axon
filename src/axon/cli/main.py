"""Axon CLI — Graph-powered code intelligence engine."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from axon import __version__
from axon.core.diff import diff_branches, format_diff
from axon.core.ingestion.pipeline import PipelineResult, run_pipeline
from axon.core.ingestion.watcher import watch_repo
from axon.core.repo_context import RepoContext, resolve_repo_context
from axon.core.storage.kuzu_backend import KuzuBackend
from axon.mcp import tools as mcp_tools
from axon.web import app as web_app_module

console = Console()
logger = logging.getLogger(__name__)
UPDATE_CHECK_INTERVAL_SECONDS = 60 * 60 * 24
UPDATE_CHECK_URL = "https://pypi.org/pypi/axoniq/json"
UPDATE_CHECK_SKIP_COMMANDS: set[str] = set()


def _load_storage(path: Path | None = None) -> "KuzuBackend":  # noqa: F821
    ctx = resolve_repo_context(path or Path.cwd())
    if not ctx.db_path.exists():
        console.print(
            f"[red]Error:[/red] No index found for [bold]{ctx.repo_name}[/bold]. "
            f"Run [cyan]axon analyze .[/cyan] first."
        )
        raise typer.Exit(code=1)
    storage = KuzuBackend()
    storage.initialize(ctx.db_path, read_only=True)
    return storage


def _update_cache_path() -> Path:
    return Path.home() / ".axon" / "update-check.json"


def _parse_version_parts(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for raw_part in version.split("."):
        digits = "".join(ch for ch in raw_part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def _is_newer_version(candidate: str, current: str) -> bool:
    return _parse_version_parts(candidate) > _parse_version_parts(current)


def _read_update_cache() -> dict | None:
    cache_path = _update_cache_path()
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_update_cache(payload: dict) -> None:
    cache_path = _update_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _fetch_latest_version() -> str | None:
    try:
        with urllib.request.urlopen(UPDATE_CHECK_URL, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return str(payload["info"]["version"])
    except (KeyError, OSError, ValueError, urllib.error.URLError):
        return None


def _get_latest_version() -> str | None:
    now = int(time.time())
    cache = _read_update_cache()
    if cache is not None:
        checked_at = int(cache.get("checked_at", 0))
        latest = cache.get("latest_version")
        if latest and now - checked_at < UPDATE_CHECK_INTERVAL_SECONDS:
            return str(latest)

    latest = _fetch_latest_version()
    if latest is not None:
        _write_update_cache({"checked_at": now, "latest_version": latest})
    return latest


def _maybe_notify_update(invoked_subcommand: str | None) -> None:
    if invoked_subcommand in UPDATE_CHECK_SKIP_COMMANDS:
        return
    latest = _get_latest_version()
    if latest and _is_newer_version(latest, __version__):
        console.print(
            f"[yellow]Update available:[/yellow] Axon {latest} "
            f"(current {__version__}). Run `pip install -U axoniq`."
        )


def _register_in_global_registry(meta: dict, repo_path: Path) -> None:
    """Write meta.json into the centralized storage directory for this repo."""
    ctx = resolve_repo_context(repo_path)
    ctx.storage_dir.mkdir(parents=True, exist_ok=True)
    registry_meta = dict(meta)
    registry_meta["slug"] = ctx.slug
    ctx.meta_path.write_text(json.dumps(registry_meta, indent=2) + "\n", encoding="utf-8")


def _build_meta(result: "PipelineResult", repo_path: Path) -> dict:  # noqa: F821
    return {
        "version": __version__,
        "name": repo_path.name,
        "path": str(repo_path),
        "stats": {
            "files": result.files,
            "symbols": result.symbols,
            "relationships": result.relationships,
            "clusters": result.clusters,
            "flows": result.processes,
            "dead_code": result.dead_code,
            "coupled_pairs": result.coupled_pairs,
            "embeddings": result.embeddings,
        },
        "last_indexed_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


app = typer.Typer(
    name="axon",
    help="Axon — Graph-powered code intelligence engine.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"Axon v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(  # noqa: N803
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Axon — Graph-powered code intelligence engine."""
    _maybe_notify_update(ctx.invoked_subcommand)


def _initialize_writable_storage(
    repo_path: Path,
    *,
    auto_index: bool = True,
) -> tuple["KuzuBackend", Path, Path]:  # noqa: F821
    ctx = resolve_repo_context(repo_path)
    storage_dir = ctx.storage_dir
    db_path = ctx.db_path

    if not auto_index and not ctx.meta_path.exists():
        console.print("[red]Error:[/red] No index found. Run [cyan]axon analyze .[/cyan] first.")
        raise typer.Exit(code=1)

    storage_dir.mkdir(parents=True, exist_ok=True)
    storage = KuzuBackend()
    storage.initialize(db_path)

    if not ctx.meta_path.exists():
        console.print("[bold]Running initial index...[/bold]")
        _, result = run_pipeline(repo_path, storage)
        meta = _build_meta(result, repo_path)
        ctx.meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return storage, storage_dir, db_path


@app.command()
def analyze(
    path: Path = typer.Argument(Path("."), help="Path to the repository to index."),
    no_embeddings: bool = typer.Option(
        False, "--no-embeddings", help="Skip vector embedding generation."
    ),
) -> None:
    """Index a repository into a knowledge graph."""
    repo_path = path.resolve()
    if not repo_path.is_dir():
        console.print(f"[red]Error:[/red] {repo_path} is not a directory.")
        raise typer.Exit(code=1)

    console.print(f"[bold]Indexing[/bold] {repo_path}")

    ctx = resolve_repo_context(repo_path)
    ctx.storage_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx.db_path

    if ctx.is_worktree:
        console.print(f"[dim]Worktree detected — storing index at {ctx.storage_dir}[/dim]")

    storage = KuzuBackend()
    storage.initialize(db_path)

    result: PipelineResult | None = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Starting...", total=None)

        def on_progress(phase: str, pct: float) -> None:
            progress.update(task, description=f"{phase} ({pct:.0%})")

        _, result = run_pipeline(
            repo_path=repo_path,
            storage=storage,
            progress_callback=on_progress,
            embeddings=not no_embeddings,
        )

    meta = _build_meta(result, repo_path)
    ctx.meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    try:
        _register_in_global_registry(meta, repo_path)
    except Exception:
        logger.debug("Failed to register repo in global registry", exc_info=True)

    console.print()
    console.print("[bold green]Indexing complete.[/bold green]")
    console.print(f"  Files:          {result.files}")
    console.print(f"  Symbols:        {result.symbols}")
    console.print(f"  Relationships:  {result.relationships}")
    if result.clusters > 0:
        console.print(f"  Clusters:       {result.clusters}")
    if result.processes > 0:
        console.print(f"  Flows:          {result.processes}")
    if result.dead_code > 0:
        console.print(f"  Dead code:      {result.dead_code}")
    if result.coupled_pairs > 0:
        console.print(f"  Coupled pairs:  {result.coupled_pairs}")
    if result.embeddings > 0:
        console.print(f"  Embeddings:     {result.embeddings}")
    console.print(f"  Duration:       {result.duration_seconds:.2f}s")

    storage.close()


@app.command()
def status() -> None:
    """Show index status for current repository."""
    ctx = resolve_repo_context()

    if not ctx.meta_path.exists():
        console.print(
            f"[red]Error:[/red] No index found for [bold]{ctx.repo_name}[/bold]. "
            f"Run [cyan]axon analyze .[/cyan] first."
        )
        raise typer.Exit(code=1)

    meta = json.loads(ctx.meta_path.read_text(encoding="utf-8"))
    stats = meta.get("stats", {})

    console.print(f"[bold]Index status for[/bold] {ctx.repo_name}")
    if ctx.is_worktree:
        console.print(f"  [dim]Worktree:[/dim]     {ctx.current_path.name} → {ctx.main_root}")
    console.print(f"  [dim]Storage:[/dim]      {ctx.storage_dir}")
    console.print(f"  Version:        {meta.get('version', '?')}")
    console.print(f"  Last indexed:   {meta.get('last_indexed_at', '?')}")
    console.print(f"  Files:          {stats.get('files', '?')}")
    console.print(f"  Symbols:        {stats.get('symbols', '?')}")
    console.print(f"  Relationships:  {stats.get('relationships', '?')}")
    if stats.get("clusters", 0) > 0:
        console.print(f"  Clusters:       {stats['clusters']}")
    if stats.get("flows", 0) > 0:
        console.print(f"  Flows:          {stats['flows']}")
    if stats.get("dead_code", 0) > 0:
        console.print(f"  Dead code:      {stats['dead_code']}")
    if stats.get("coupled_pairs", 0) > 0:
        console.print(f"  Coupled pairs:  {stats['coupled_pairs']}")


@app.command(name="list")
def list_repos() -> None:
    """List all indexed repositories."""
    result = mcp_tools.handle_list_repos()
    console.print(result)


@app.command()
def clean(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
) -> None:
    """Delete index for current repository."""
    ctx = resolve_repo_context()

    if not ctx.storage_dir.exists():
        console.print(f"[red]Error:[/red] No index found for {ctx.repo_name}. Nothing to clean.")
        raise typer.Exit(code=1)

    if not force:
        confirm = typer.confirm(f"Delete index for {ctx.repo_name} at {ctx.storage_dir}?")
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit()

    shutil.rmtree(ctx.storage_dir)
    console.print(f"[green]Deleted[/green] {ctx.storage_dir}")


@app.command()
def query(
    q: str = typer.Argument(..., help="Search query for the knowledge graph."),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum number of results."),
) -> None:
    """Search the knowledge graph."""
    storage = _load_storage()
    result = mcp_tools.handle_query(storage, q, limit=limit)
    console.print(result)
    storage.close()


@app.command()
def context(
    name: str = typer.Argument(..., help="Symbol name to inspect."),
) -> None:
    """Show 360-degree view of a symbol."""
    storage = _load_storage()
    result = mcp_tools.handle_context(storage, name)
    console.print(result)
    storage.close()


@app.command()
def impact(
    target: str = typer.Argument(..., help="Symbol to analyze blast radius for."),
    depth: int = typer.Option(3, "--depth", "-d", min=1, max=10, help="Traversal depth (1-10)."),
) -> None:
    """Show blast radius of changing a symbol."""
    storage = _load_storage()
    result = mcp_tools.handle_impact(storage, target, depth=depth)
    console.print(result)
    storage.close()


@app.command(name="dead-code")
def dead_code() -> None:
    """List all detected dead code."""
    storage = _load_storage()
    result = mcp_tools.handle_dead_code(storage)
    console.print(result)
    storage.close()


@app.command()
def cypher(
    query: str = typer.Argument(..., help="Raw Cypher query to execute."),
) -> None:
    """Execute raw Cypher against the knowledge graph."""
    storage = _load_storage()
    result = mcp_tools.handle_cypher(storage, query)
    console.print(result)
    storage.close()


@app.command()
def watch() -> None:
    """Watch mode — re-index on file changes. Must be run from the main repo root."""
    ctx = resolve_repo_context()

    # Block worktrees — they share the main repo's database
    if ctx.is_worktree:
        console.print(
            f"[yellow]Watch mode is not supported in git worktrees.[/yellow]\n"
            f"\n"
            f"The database is shared with the main repo at:\n"
            f"  {ctx.main_root}\n"
            f"\n"
            f"Options:\n"
            f"  • Watch from main repo:  [cyan]cd {ctx.main_root} && axon watch[/cyan]\n"
            f"  • Re-index after changes: [cyan]axon analyze .[/cyan]"
        )
        raise typer.Exit(code=1)

    ctx.storage_dir.mkdir(parents=True, exist_ok=True)

    # Single-instance enforcement via PID file
    pid_file = ctx.storage_dir / "watch.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _pid_is_alive(pid):
                console.print(
                    f"[yellow]Watcher already running[/yellow] for {ctx.repo_name} (PID {pid}).\n"
                    f"Kill it first: [cyan]kill {pid}[/cyan]"
                )
                raise typer.Exit(code=1)
        except (ValueError, OSError):
            pass
        pid_file.unlink(missing_ok=True)

    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    storage = KuzuBackend()
    storage.initialize(ctx.db_path)

    if not ctx.meta_path.exists():
        console.print("[bold]Running initial index...[/bold]")
        _, result = run_pipeline(ctx.main_root, storage)
        meta = _build_meta(result, ctx.main_root)
        ctx.meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    console.print(f"[bold]Watching[/bold] {ctx.main_root} for changes (Ctrl+C to stop)")

    try:
        asyncio.run(watch_repo(ctx.main_root, storage))
    except KeyboardInterrupt:
        console.print("\n[bold]Watch stopped.[/bold]")
    finally:
        pid_file.unlink(missing_ok=True)
        storage.close()


@app.command()
def diff(
    branch_range: str = typer.Argument(..., help="Branch range (e.g. main..feature)."),
) -> None:
    """Structural branch comparison."""
    ctx = resolve_repo_context()
    try:
        result = diff_branches(ctx.main_root, branch_range)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(format_diff(result))


@app.command()
def ui(
    port: int = typer.Option(8420, "--port", "-p", help="Port to serve on."),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open browser."),
    watch_files: bool = typer.Option(False, "--watch", "-w", help="Enable live file watching."),
    dev: bool = typer.Option(False, "--dev", help="Proxy to Vite dev server for HMR."),
) -> None:
    """Launch the Axon web UI."""
    ctx = resolve_repo_context()

    if not ctx.db_path.exists():
        console.print(
            f"[red]Error:[/red] No index found for [bold]{ctx.repo_name}[/bold]. "
            f"Run [cyan]axon analyze .[/cyan] first."
        )
        raise typer.Exit(code=1)

    web_app = web_app_module.create_app(
        db_path=ctx.db_path,
        repo_path=ctx.main_root,
        watch=watch_files,
        dev=dev,
    )

    url = f"http://localhost:{port}"
    if not no_open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    console.print(f"[bold green]Axon UI[/bold green] running at {url}")
    if watch_files:
        console.print("[dim]File watching enabled — graph updates on save[/dim]")
    if dev:
        console.print("[dim]Dev mode — proxying to Vite on :5173[/dim]")

    if watch_files:

        async def _run() -> None:
            config = uvicorn.Config(web_app, host="127.0.0.1", port=port, log_level="warning")
            server = uvicorn.Server(config)
            stop = asyncio.Event()

            async def _serve() -> None:
                await server.serve()
                stop.set()

            await asyncio.gather(
                _serve(),
                watch_repo(ctx.main_root, web_app.state.storage, stop_event=stop),
            )

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            console.print("\n[bold]UI stopped.[/bold]")
    else:
        uvicorn.run(web_app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()

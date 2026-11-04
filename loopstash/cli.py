"""Typer CLI interface and Rich terminal UX for LOOPSTASH."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from loopstash import __app_name__, __version__
from loopstash.backends.gallery_dl import GalleryDlBackend
from loopstash.config import (
    get_config_file_path,
    load_config,
    reset_config,
    update_config_key,
)
from loopstash.database import Database
from loopstash.diagnostics import run_all_diagnostics
from loopstash.downloader import Downloader
from loopstash.duplicate import DuplicateDetector
from loopstash.logging import setup_logging
from loopstash.models import DownloadProgress, DownloadStatus
from loopstash.retry import RetryPolicy
from loopstash.storage import StorageManager
from loopstash.utils.validation import validate_instagram_url

app = typer.Typer(
    name=__app_name__,
    help="LOOPSTASH: Collect. Preserve. Organise. A lightweight media archiving utility.",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


def version_callback(value: bool):
    if value:
        console.print(
            f"[bold cyan]LOOPSTASH[/bold cyan] v{__version__} "
            f"— [italic]Collect. Preserve. Organise.[/italic]"
        )
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show LOOPSTASH version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
):
    """LOOPSTASH: Collect. Preserve. Organise."""
    pass


def print_header(title: str = "LOOPSTASH", subtitle: str = "Collect. Preserve. Organise.") -> None:
    """Display clean minimalist application header."""
    header_text = f"[bold cyan]{title}[/bold cyan]\n[dim]{subtitle}[/dim]"
    console.print(Panel(header_text, border_style="cyan", box=box.ROUNDED))


@app.command(name="download")
def download(
    profile_url: str = typer.Argument(
        ...,
        help="Instagram profile username or URL (e.g., @username, https://instagram.com/user/).",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Custom destination directory for media archives."
    ),
    browser: Optional[str] = typer.Option(
        None,
        "--browser",
        "-b",
        help="Browser to read session cookies from (firefox, chrome, chromium, etc.).",
    ),
    max_retries: Optional[int] = typer.Option(
        None, "--max-retries", help="Max retry attempts for transient errors (default: 5)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force redownload of previously completed Reels."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Discover Reels without downloading media files."
    ),
    max_items: Optional[int] = typer.Option(
        None, "--max-items", "-n", help="Limit number of Reels to extract."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debug logging."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
):
    """Download all accessible Instagram Reels from a public profile."""
    cfg = load_config()
    setup_logging(verbose=verbose or cfg.verbose, no_color=no_color or cfg.no_color)

    # Validate profile input early
    is_valid, username, normalized_url, err = validate_instagram_url(profile_url)
    if not is_valid or not username or not normalized_url:
        console.print(f"[bold red]Error:[/bold red] {err}")
        raise typer.Exit(code=1)

    if max_retries is not None and max_retries < 0:
        console.print("[bold red]Error:[/bold red] --max-retries must be non-negative (>= 0).")
        raise typer.Exit(code=1)

    if max_items is not None and max_items <= 0:
        console.print("[bold red]Error:[/bold red] --max-items must be greater than 0.")
        raise typer.Exit(code=1)

    print_header()

    target_dir = output or Path(cfg.download_dir)
    effective_browser = browser or cfg.browser
    effective_retries = max_retries if max_retries is not None else cfg.max_retries

    console.print(f"[bold]Profile:[/bold] @{username}")
    console.print(f"[bold]Target URL:[/bold] {normalized_url}")
    console.print(f"[bold]Destination:[/bold] {target_dir}")
    if effective_browser:
        console.print(f"[bold]Authentication:[/bold] Browser cookies ({effective_browser})")
    else:
        console.print("[bold]Authentication:[/bold] Anonymous public access")
    if dry_run:
        console.print(
            "[bold yellow]Mode:[/bold yellow] Dry-run simulation (no files will be written)"
        )
    console.print()

    db = Database()
    backend = GalleryDlBackend()
    storage = StorageManager(target_dir)
    retry_policy = RetryPolicy(max_retries=effective_retries)

    downloader = Downloader(
        backend=backend,
        storage=storage,
        db=db,
        retry_policy=retry_policy,
        rate_limit_pause=cfg.rate_limit_pause,
        min_free_space_mb=cfg.min_free_space_mb,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Discovering Reels...", total=100)

        def progress_cb(p: DownloadProgress):
            if p.total_reels > 0:
                progress.update(
                    task,
                    total=p.total_reels,
                    completed=p.completed_reels + p.duplicate_reels + p.failed_reels,
                    description=(
                        f"[cyan]Downloading: {p.current_file or p.shortcode} "
                        f"({p.completed_reels}/{p.total_reels})"
                    ),
                )

        try:
            summary = downloader.download_profile(
                profile_input=profile_url,
                browser=effective_browser,
                force=force,
                dry_run=dry_run,
                max_items=max_items,
                progress_callback=progress_cb,
            )
            progress.update(task, completed=summary.total_discovered, description="[green]Done!")
        except Exception as e:
            console.print(f"\n[bold red]Operation Failed:[/bold red] {e}")
            raise typer.Exit(code=1) from e

    console.print()
    table = Table(title="Download Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Count / Value", style="bold")
    table.add_row("Discovered Reels", str(summary.total_discovered))
    table.add_row("Downloaded", f"[green]{summary.downloaded}[/green]")
    table.add_row("Skipped (Duplicates)", f"[dim]{summary.skipped_duplicates}[/dim]")
    table.add_row("Failed", f"[red]{summary.failed}[/red]" if summary.failed else "0")
    table.add_row("Total Media Size", f"{summary.total_bytes / (1024 * 1024):.2f} MB")
    console.print(table)

    if summary.errors:
        console.print("\n[bold red]Failed Items:[/bold red]")
        for code, err_msg in summary.errors[:10]:
            console.print(f"  • [yellow]{code}[/yellow]: {err_msg}")
        if len(summary.errors) > 10:
            console.print(
                f"  ... and {len(summary.errors) - 10} more. Run 'loopstash resume' to retry."
            )


@app.command(name="resume")
def resume(
    profile_url: Optional[str] = typer.Argument(
        None, help="Optional profile username to filter resume."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Custom destination directory."
    ),
    browser: Optional[str] = typer.Option(
        None, "--browser", "-b", help="Browser to read cookies from."
    ),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Max retry attempts."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging."),
):
    """Resume interrupted or failed Reel downloads."""
    cfg = load_config()
    setup_logging(verbose=verbose or cfg.verbose)

    target_user = None
    if profile_url:
        is_valid, target_user, _, err = validate_instagram_url(profile_url)
        if not is_valid or not target_user:
            console.print(f"[bold red]Error:[/bold red] {err}")
            raise typer.Exit(code=1)

    if max_retries is not None and max_retries < 0:
        console.print("[bold red]Error:[/bold red] --max-retries must be non-negative (>= 0).")
        raise typer.Exit(code=1)

    print_header(subtitle="Resuming Interrupted Downloads")

    target_dir = output or Path(cfg.download_dir)
    effective_browser = browser or cfg.browser
    effective_retries = max_retries if max_retries is not None else cfg.max_retries

    db = Database()
    backend = GalleryDlBackend()
    storage = StorageManager(target_dir)
    retry_policy = RetryPolicy(max_retries=effective_retries)

    downloader = Downloader(
        backend=backend,
        storage=storage,
        db=db,
        retry_policy=retry_policy,
        rate_limit_pause=cfg.rate_limit_pause,
        min_free_space_mb=cfg.min_free_space_mb,
    )

    try:
        summaries = downloader.resume(
            username=target_user,
            browser=effective_browser,
        )
        if not summaries:
            console.print(
                "[green]No interrupted or pending downloads found. All queues are clear![/green]"
            )
            return

        for s in summaries:
            console.print(
                f"[bold cyan]@{s.username}:[/bold cyan] "
                f"Resumed {s.downloaded} downloads, {s.failed} failed."
            )
    except Exception as e:
        console.print(f"[bold red]Resume error:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(name="profiles")
def list_profiles():
    """List all tracked Instagram profiles and their archiving statistics."""
    print_header(subtitle="Tracked Profiles")
    db = Database()
    profiles = db.get_all_profiles()

    if not profiles:
        console.print(
            "[dim]No profiles tracked yet. "
            "Run 'loopstash download <username>' to start archiving.[/dim]"
        )
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Username", style="cyan bold")
    table.add_column("Discovered", justify="right")
    table.add_column("Archived", justify="right", style="green")
    table.add_column("Duplicates", justify="right", style="dim")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Last Sync", style="dim")

    for p in profiles:
        table.add_row(
            f"@{p.username}",
            str(p.total_discovered),
            str(p.completed_count),
            str(p.duplicate_count),
            str(p.failed_count),
            (p.last_sync or "Never")[:19],
        )

    console.print(table)


@app.command(name="status")
def status(
    profile_url: Optional[str] = typer.Argument(None, help="Optional profile username to inspect."),
):
    """Display overall system stats or profile-specific status."""
    print_header(subtitle="Archive Status")
    db = Database()

    if profile_url:
        is_valid, username, _, err = validate_instagram_url(profile_url)
        if not is_valid or not username:
            console.print(f"[bold red]Error:[/bold red] {err}")
            raise typer.Exit(code=1)

        prof = db.get_profile(username)
        if not prof:
            console.print(f"[yellow]No records found for @{username}.[/yellow]")
            return

        table = Table(title=f"Status for @{username}", box=box.ROUNDED)
        table.add_column("Attribute", style="cyan")
        table.add_column("Value", style="bold")
        table.add_row("Profile URL", prof.profile_url)
        table.add_row("First Seen", prof.first_seen[:19])
        table.add_row("Last Synchronized", (prof.last_sync or "Never")[:19])
        table.add_row("Total Discovered", str(prof.total_discovered))
        table.add_row("Completed (On Disk)", f"[green]{prof.completed_count}[/green]")
        table.add_row("Failed", f"[red]{prof.failed_count}[/red]")
        table.add_row("Duplicates Skipped", f"[dim]{prof.duplicate_count}[/dim]")
        console.print(table)
    else:
        stats = db.get_statistics()
        table = Table(title="Global Archive Statistics", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold")
        table.add_row("Tracked Profiles", str(stats["total_profiles"]))
        table.add_row("Total Cataloged Reels", str(stats["total_reels"]))
        table.add_row("Successfully Archived", f"[green]{stats['completed_reels']}[/green]")
        table.add_row("Failed", f"[red]{stats['failed_reels']}[/red]")
        table.add_row("Duplicates Skipped", f"[dim]{stats['duplicate_reels']}[/dim]")
        table.add_row("Total Archive Media Size", f"{stats['total_bytes'] / (1024 * 1024):.2f} MB")
        console.print(table)


@app.command(name="history")
def history(
    limit: int = typer.Option(25, "--limit", "-n", help="Number of records to display."),
):
    """View recent Reel download activity."""
    print_header(subtitle="Download History")
    db = Database()
    records = db.get_history(limit=limit)

    if not records:
        console.print("[dim]No download history available yet.[/dim]")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Shortcode", style="cyan")
    table.add_column("Profile", style="bold")
    table.add_column("Status")
    table.add_column("Size", justify="right")
    table.add_column("Updated At", style="dim")

    for r in records:
        status_style = {
            DownloadStatus.COMPLETED: "[green]COMPLETED[/green]",
            DownloadStatus.FAILED: "[red]FAILED[/red]",
            DownloadStatus.DOWNLOADING: "[yellow]DOWNLOADING[/yellow]",
            DownloadStatus.PENDING: "[dim]PENDING[/dim]",
            DownloadStatus.SKIPPED_DUPLICATE: "[dim]DUPLICATE[/dim]",
        }.get(r.status, r.status.value)

        size_str = f"{(r.file_size or 0) / 1024:.1f} KB" if r.file_size else "-"
        table.add_row(
            r.shortcode,
            f"@{r.username}",
            status_style,
            size_str,
            (r.updated_at or "")[:19],
        )

    console.print(table)


@app.command(name="verify")
def verify_cmd(
    profile_url: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Verify only a specific profile."
    ),
):
    """Verify on-disk archive files against recorded SHA-256 hashes."""
    print_header(subtitle="Archive Integrity Verification")
    db = Database()
    detector = DuplicateDetector(db)

    target_user = None
    if profile_url:
        is_valid, target_user, _, err = validate_instagram_url(profile_url)
        if not is_valid or not target_user:
            console.print(f"[bold red]Error:[/bold red] {err}")
            raise typer.Exit(code=1)

    with console.status("[cyan]Verifying media files and computing SHA-256 digests...[/cyan]"):
        results = detector.verify_archive(username=target_user)

    table = Table(title="Integrity Verification Results", box=box.ROUNDED)
    table.add_column("Check", style="cyan")
    table.add_column("Count", style="bold")
    table.add_row("Total Checked", str(results["total_checked"]))
    table.add_row("Verified Intact", f"[green]{results['verified']}[/green]")
    table.add_row(
        "Missing Files", f"[red]{results['missing']}[/red]" if results["missing"] else "0"
    )
    table.add_row(
        "Corrupted / Modified",
        f"[red]{results['corrupted']}[/red]" if results["corrupted"] else "0",
    )
    console.print(table)

    if results["missing"] > 0 or results["corrupted"] > 0:
        console.print("\n[bold yellow]Attention Required:[/bold yellow]")
        issue_items = [(code, st, reason) for code, st, reason in results["details"] if st != "OK"]
        for code, status, reason in issue_items[:20]:
            console.print(f"  • [bold red]{code}[/bold red] ({status}): {reason}")
        if len(issue_items) > 20:
            console.print(f"  ... and {len(issue_items) - 20} more issues.")


@app.command(name="doctor")
def doctor():
    """Run system and environment diagnostic checks."""
    print_header(subtitle="System Diagnostics")
    checks = run_all_diagnostics()

    table = Table(box=box.ROUNDED)
    table.add_column("Diagnostic Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    all_pass = True
    for c in checks:
        if c.status == "PASS":
            status_text = "[bold green]PASS[/bold green]"
        elif c.status == "WARN":
            status_text = "[bold yellow]WARN[/bold yellow]"
        else:
            status_text = "[bold red]FAIL[/bold red]"
            all_pass = False

        table.add_row(c.name, status_text, c.details)

    console.print(table)

    remediations = [c for c in checks if c.remediation]
    if remediations:
        console.print("\n[bold yellow]Recommended Actions:[/bold yellow]")
        for c in remediations:
            console.print(f"  • [bold]{c.name}:[/bold] {c.remediation}")
    elif all_pass:
        console.print(
            "\n[bold green]All systems nominal. LOOPSTASH is ready to archive![/bold green]"
        )


@app.command(name="config")
def config_cmd(
    set_key: Optional[str] = typer.Option(None, "--set", help="Update a key in KEY=VALUE format."),
    show: bool = typer.Option(False, "--show", help="Display current configuration."),
    reset: bool = typer.Option(False, "--reset", help="Reset configuration to defaults."),
):
    """View or update LOOPSTASH application settings."""
    cfg_file = get_config_file_path()

    if reset:
        reset_config()
        console.print(f"[green]Configuration reset to defaults at '{cfg_file}'.[/green]")
        return

    if set_key:
        if "=" not in set_key:
            console.print(
                "[bold red]Error:[/bold red] Setting must be in KEY=VALUE format "
                "(e.g., --set max_retries=10)"
            )
            raise typer.Exit(code=1)
        k, v = set_key.split("=", 1)
        k = k.strip()
        v = v.strip()
        try:
            update_config_key(k, v)
            console.print(f"[green]Successfully updated '{k}' to '{v}'.[/green]")
        except Exception as e:
            console.print(f"[bold red]Configuration Error:[/bold red] {e}")
            raise typer.Exit(code=1) from e
        return

    # Show config by default
    cfg = load_config()
    print_header(subtitle="Application Configuration")
    console.print(f"[dim]File: {cfg_file}[/dim]\n")

    table = Table(box=box.ROUNDED)
    table.add_column("Setting", style="cyan bold")
    table.add_column("Value", style="bold")
    table.add_row("download_dir", cfg.download_dir)
    table.add_row("max_retries", str(cfg.max_retries))
    table.add_row("browser", str(cfg.browser or "None (Anonymous)"))
    table.add_row("rate_limit_pause", f"{cfg.rate_limit_pause}s")
    table.add_row("min_free_space_mb", f"{cfg.min_free_space_mb} MB")
    table.add_row("backend", cfg.backend)
    table.add_row("verbose", str(cfg.verbose))
    table.add_row("no_color", str(cfg.no_color))
    console.print(table)

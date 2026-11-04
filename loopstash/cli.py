"""Typer CLI interface and Rich terminal UX for LOOPSTASH."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm
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
from loopstash.library import LibraryManager, MetadataEditor
from loopstash.logging import setup_logging
from loopstash.models import DownloadProgress, DownloadStatus, PublishStatus
from loopstash.publish import (
    PublishingQueue,
    QuotaManager,
    YouTubeAuthManager,
    YouTubeScheduler,
)
from loopstash.retry import RetryPolicy
from loopstash.storage import StorageManager
from loopstash.utils.validation import validate_instagram_url

app = typer.Typer(
    name=__app_name__,
    help="LOOPSTASH: Collect. Preserve. Organise. Media archiving & publishing application.",
    add_completion=False,
    no_args_is_help=True,
)

archive_app = typer.Typer(
    help="Instagram Reels archiving, recovery, and download operations.",
    add_completion=False,
    no_args_is_help=True,
)
library_app = typer.Typer(
    help="Media library management, inspection, search, and metadata editing.",
    add_completion=False,
    no_args_is_help=True,
)
publish_app = typer.Typer(
    help="YouTube publishing engine, OAuth connection, and immediate public release.",
    add_completion=False,
    no_args_is_help=True,
)
schedule_app = typer.Typer(
    help="Publication calendar generation, scheduling, and public release slots.",
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(archive_app, name="archive")
app.add_typer(library_app, name="library")
app.add_typer(publish_app, name="publish")
app.add_typer(schedule_app, name="schedule")

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


# ============================================================================
# ARCHIVE COMMANDS (Instagram Reels)
# ============================================================================


def _execute_instagram_download(
    profile_url: str,
    output: Optional[Path] = None,
    browser: Optional[str] = None,
    max_retries: Optional[int] = None,
    force: bool = False,
    dry_run: bool = False,
    max_items: Optional[int] = None,
    verbose: bool = False,
    no_color: bool = False,
) -> None:
    cfg = load_config()
    setup_logging(verbose=verbose or cfg.verbose, no_color=no_color or cfg.no_color)

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

    print_header(title="LOOPSTASH ARCHIVER", subtitle=f"Target: @{username}")

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
        console.print("[bold yellow]Mode:[/bold yellow] Dry-run simulation (no files written)")
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
                f"  ... and {len(summary.errors) - 10} more. "
                "Run 'loopstash archive resume' to retry."
            )


@archive_app.command(name="instagram")
def archive_instagram_cmd(
    profile_url: str = typer.Argument(
        ...,
        help="Instagram profile username or URL (e.g. @username, https://instagram.com/user/).",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Custom destination directory."
    ),
    browser: Optional[str] = typer.Option(
        None, "--browser", "-b", help="Browser to read session cookies from."
    ),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Max retry attempts."),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force redownload of previously archived Reels."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Discover Reels without downloading media."
    ),
    max_items: Optional[int] = typer.Option(
        None, "--max-items", "-n", help="Limit number of Reels to extract."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose debug logging."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
):
    """Bulk download accessible Reels from a public Instagram profile."""
    _execute_instagram_download(
        profile_url=profile_url,
        output=output,
        browser=browser,
        max_retries=max_retries,
        force=force,
        dry_run=dry_run,
        max_items=max_items,
        verbose=verbose,
        no_color=no_color,
    )


@archive_app.command(name="resume")
def archive_resume_cmd(
    profile_url: Optional[str] = typer.Argument(
        None, help="Optional profile username to filter resume."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Custom destination directory."
    ),
    browser: Optional[str] = typer.Option(
        None, "--browser", "-b", help="Browser to read session cookies from."
    ),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Max retry attempts."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
):
    """Resume interrupted or failed Instagram Reel downloads."""
    cfg = load_config()
    setup_logging(verbose=verbose or cfg.verbose)

    target_user = None
    if profile_url:
        is_valid, target_user, _, err = validate_instagram_url(profile_url)
        if not is_valid or not target_user:
            console.print(f"[bold red]Error:[/bold red] {err}")
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

    summaries = downloader.resume(username=target_user, browser=effective_browser)
    if not summaries:
        console.print(
            "[green]No interrupted or pending downloads found. All queues are clear![/green]"
        )
        return

    for s in summaries:
        console.print(
            f"[bold cyan]@{s.username}:[/bold cyan] Resumed {s.downloaded} downloads, "
            f"{s.failed} failed."
        )


@archive_app.command(name="status")
def archive_status_cmd(
    profile_url: Optional[str] = typer.Argument(None, help="Optional profile username to inspect."),
):
    """Display overall archive statistics or inspect a specific profile."""
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


@archive_app.command(name="profiles")
def archive_profiles_cmd():
    """List all tracked Instagram profiles and their archiving statistics."""
    print_header(subtitle="Tracked Profiles")
    db = Database()
    profiles = db.get_all_profiles()

    if not profiles:
        console.print(
            "[dim]No profiles tracked yet. "
            "Run 'loopstash archive instagram <username>' to start archiving.[/dim]"
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


@archive_app.command(name="history")
def archive_history_cmd(
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


@archive_app.command(name="verify")
def archive_verify_cmd(
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


# Top-level aliases for download and resume
@app.command(name="download", hidden=True)
def download_alias(
    profile_url: str = typer.Argument(..., help="Instagram profile username or URL."),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    browser: Optional[str] = typer.Option(None, "--browser", "-b"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries"),
    force: bool = typer.Option(False, "--force", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_items: Optional[int] = typer.Option(None, "--max-items", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    no_color: bool = typer.Option(False, "--no-color"),
):
    """Download accessible Instagram Reels (alias for 'loopstash archive instagram')."""
    _execute_instagram_download(
        profile_url=profile_url,
        output=output,
        browser=browser,
        max_retries=max_retries,
        force=force,
        dry_run=dry_run,
        max_items=max_items,
        verbose=verbose,
        no_color=no_color,
    )


@app.command(name="resume", hidden=True)
def resume_alias(
    profile_url: Optional[str] = typer.Argument(None),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    browser: Optional[str] = typer.Option(None, "--browser", "-b"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Resume interrupted Reel downloads (alias for 'loopstash archive resume')."""
    archive_resume_cmd(
        profile_url=profile_url,
        output=output,
        browser=browser,
        max_retries=max_retries,
        verbose=verbose,
    )


# ============================================================================
# MEDIA LIBRARY COMMANDS
# ============================================================================


@library_app.command(name="list")
def library_list_cmd(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Filter by creator."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max items to display."),
    offset: int = typer.Option(0, "--offset", help="Number of items to skip."),
):
    """List videos in the media library."""
    print_header(subtitle="Media Library")
    db = Database()
    mgr = LibraryManager(db)
    items = mgr.list_items(username=profile, limit=limit, offset=offset)

    if not items:
        console.print("[dim]Media library is empty. Archive reels or import videos to begin.[/dim]")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Media ID", style="cyan bold")
    table.add_column("Creator", style="bold")
    table.add_column("Title")
    table.add_column("Size", justify="right")
    table.add_column("Rights", justify="center")
    table.add_column("Added At", style="dim")

    for it in items:
        pub = db.get_publishing_metadata(it.id)
        title = pub.title if pub and pub.title else (it.title or "-")
        size_str = f"{(it.file_size or 0) / (1024 * 1024):.1f} MB" if it.file_size else "-"
        rights_badge = (
            "[green]CONFIRMED[/green]"
            if (pub and pub.rights_confirmed)
            else "[yellow]UNCONFIRMED[/yellow]"
        )
        table.add_row(
            it.id,
            f"@{it.username}" if it.username else "-",
            title[:40],
            size_str,
            rights_badge,
            (it.download_timestamp or it.created_at or "")[:10],
        )

    console.print(table)


@library_app.command(name="search")
def library_search_cmd(
    query: str = typer.Argument(..., help="Search query (matches ID, username, title, tags)."),
    limit: int = typer.Option(25, "--limit", "-n", help="Max search results."),
):
    """Search media library items across titles, creators, and hashtags."""
    print_header(subtitle=f"Library Search: '{query}'")
    db = Database()
    mgr = LibraryManager(db)
    items = mgr.search(query, limit=limit)

    if not items:
        console.print(f"[yellow]No library items found matching '{query}'.[/yellow]")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Media ID", style="cyan bold")
    table.add_column("Creator", style="bold")
    table.add_column("Title")
    table.add_column("Size", justify="right")

    for it in items:
        pub = db.get_publishing_metadata(it.id)
        title = pub.title if pub and pub.title else (it.title or "-")
        size_str = f"{(it.file_size or 0) / (1024 * 1024):.1f} MB" if it.file_size else "-"
        table.add_row(it.id, f"@{it.username}", title[:50], size_str)

    console.print(table)


@library_app.command(name="inspect")
def library_inspect_cmd(
    media_id: str = typer.Argument(..., help="Media ID or Reel shortcode to inspect."),
):
    """Display comprehensive details, publishing metadata, and file health for a video."""
    print_header(subtitle=f"Inspecting Media: {media_id}")
    db = Database()
    mgr = LibraryManager(db)
    details = mgr.get_item_details(media_id)

    if not details:
        console.print(f"[bold red]Error:[/bold red] Media '{media_id}' not found in library.")
        raise typer.Exit(code=1)

    item = details["media_item"]
    pub = details["publishing_metadata"]

    table = Table(box=box.ROUNDED, title="Asset Overview")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row("Media ID", item.id)
    table.add_row("Creator", f"@{item.username}" if item.username else "Unknown")
    table.add_row("Source Platform", item.source_platform)
    table.add_row("Local Path", str(item.file_path or "None"))
    table.add_row(
        "File On Disk",
        "[green]Present[/green]" if details["file_exists"] else "[red]Missing[/red]",
    )
    table.add_row("File Size", f"{(item.file_size or 0) / (1024 * 1024):.2f} MB")
    table.add_row("SHA-256", (item.sha256 or "None")[:20] + "...")
    if details["hash_matches"] is not None:
        table.add_row(
            "Integrity Verified",
            "[green]MATCH[/green]" if details["hash_matches"] else "[red]CORRUPTED / CHANGED[/red]",
        )
    console.print(table)

    if pub:
        pub_table = Table(box=box.ROUNDED, title="Publishing Metadata (YouTube)")
        pub_table.add_column("Field", style="cyan")
        pub_table.add_column("Configured Value", style="bold")
        pub_table.add_row("Title", pub.title)
        pub_table.add_row("Description", (pub.description[:100] + "...") if pub.description else "")
        pub_table.add_row("Tags", ", ".join(pub.tags) if pub.tags else "None")
        pub_table.add_row("Privacy Status", pub.privacy_status.upper())
        pub_table.add_row("Made For Kids", "Yes" if pub.made_for_kids else "No")
        pub_table.add_row(
            "Rights Confirmed",
            "[green]Yes[/green]"
            if pub.rights_confirmed
            else "[red]No (Confirmation Required)[/red]",
        )
        console.print(pub_table)


@library_app.command(name="edit")
def library_edit_cmd(
    media_id: str = typer.Argument(..., help="Media ID or shortcode to edit."),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="New video title (max 100)."),
    description: Optional[str] = typer.Option(
        None, "--desc", "-d", help="New description (max 5000)."
    ),
    tags: Optional[str] = typer.Option(
        None, "--tags", help="Comma-separated tags (e.g. 'tech,news,demo')."
    ),
    category_id: Optional[str] = typer.Option(
        None, "--category", help="YouTube numeric category ID (default: 22)."
    ),
    privacy: Optional[str] = typer.Option(
        None, "--privacy", help="Privacy status (public, private, unlisted)."
    ),
    for_kids: Optional[bool] = typer.Option(None, "--for-kids", help="Declare made for kids."),
    not_for_kids: Optional[bool] = typer.Option(
        None, "--not-for-kids", help="Declare not made for kids."
    ),
    confirm_rights: bool = typer.Option(
        False, "--confirm-rights", help="Confirm ownership or republishing license."
    ),
):
    """Edit publishing metadata for a media library video."""
    db = Database()
    editor = MetadataEditor(db)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags is not None else None
    kids_flag = None
    if for_kids:
        kids_flag = True
    elif not_for_kids:
        kids_flag = False

    try:
        updated = editor.edit(
            media_id=media_id,
            title=title,
            description=description,
            tags=tag_list,
            category_id=category_id,
            privacy_status=privacy,
            made_for_kids=kids_flag,
            rights_confirmed=True if confirm_rights else None,
        )
        console.print(f"[green]Successfully updated publishing metadata for '{media_id}':[/green]")
        console.print(f"  • Title: [bold]{updated.title}[/bold]")
        console.print(f"  • Privacy: [bold]{updated.privacy_status}[/bold]")
        console.print(
            f"  • Rights Confirmed: [bold]{'Yes' if updated.rights_confirmed else 'No'}[/bold]"
        )
    except Exception as e:
        console.print(f"[bold red]Edit Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@library_app.command(name="verify")
def library_verify_cmd(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Filter by creator."),
):
    """Verify library files on disk and detect missing or corrupted assets."""
    print_header(subtitle="Library Integrity Check")
    db = Database()
    mgr = LibraryManager(db)

    with console.status("[cyan]Verifying library files on disk and checking hashes...[/cyan]"):
        results = mgr.verify_library(username=profile)

    table = Table(title="Library Health", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="bold")
    table.add_row("Total Assets", str(results["total_checked"]))
    table.add_row("Intact On Disk", f"[green]{results['verified']}[/green]")
    table.add_row(
        "Missing Files", f"[red]{results['missing']}[/red]" if results["missing"] else "0"
    )
    table.add_row(
        "Corrupted / Mismatched",
        f"[red]{results['corrupted']}[/red]" if results["corrupted"] else "0",
    )
    console.print(table)


@library_app.command(name="export")
def library_export_cmd(
    output: Path = typer.Option(
        Path("library_export.json"), "--output", "-o", help="Output file path."
    ),
    export_format: str = typer.Option("json", "--format", "-f", help="Format: 'json' or 'csv'."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Filter by creator."),
):
    """Export media library catalog and metadata to JSON or CSV."""
    db = Database()
    mgr = LibraryManager(db)
    try:
        out_file = mgr.export_metadata(
            output_path=output, export_format=export_format, username=profile
        )
        console.print(f"[green]Library catalog exported successfully to '{out_file}'.[/green]")
    except Exception as e:
        console.print(f"[bold red]Export Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@library_app.command(name="import")
def library_import_cmd(
    directory: Path = typer.Argument(..., help="Path to local folder containing video files."),
    username: str = typer.Option("local_import", "--username", "-u", help="Creator username tag."),
):
    """Scan and catalog local video files and sidecars into the media library."""
    db = Database()
    mgr = LibraryManager(db)
    print_header(subtitle="Importing Local Media")
    try:
        res = mgr.import_directory(directory=directory, username=username)
        console.print(f"[green]Import Complete:[/green] Added {res['imported']} new videos.")
        if res["skipped_duplicates"]:
            console.print(
                f"[dim]Skipped {res['skipped_duplicates']} existing duplicate files.[/dim]"
            )
    except Exception as e:
        console.print(f"[bold red]Import Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e


# ============================================================================
# PUBLISH COMMANDS (YouTube OAuth & Uploads)
# ============================================================================


@publish_app.command(name="auth")
def publish_auth_cmd(
    platform: str = typer.Argument("youtube", help="Publishing platform (youtube)."),
    credentials: Path = typer.Option(
        Path("client_secret.json"),
        "--credentials",
        "-c",
        help="Path to Google Cloud OAuth client credentials JSON.",
    ),
    manual: bool = typer.Option(
        False, "--manual", help="Run console-based auth flow for headless servers."
    ),
):
    """Connect user's official YouTube channel via OAuth 2.0."""
    if platform.lower() != "youtube":
        console.print(f"[bold red]Error:[/bold red] Platform '{platform}' is not yet supported.")
        raise typer.Exit(code=1)

    print_header(subtitle="YouTube OAuth 2.0 Authorization")
    db = Database()
    auth_mgr = YouTubeAuthManager(db)

    try:
        account = auth_mgr.authorize_account(client_secrets_path=credentials, manual=manual)
        console.print(
            f"\n[bold green]Successfully authorized YouTube channel:[/bold green] "
            f"[bold cyan]{account.channel_title}[/bold cyan] ({account.channel_id})"
        )
        console.print("[dim]OAuth credentials stored securely via OS keyring/token store.[/dim]")
    except Exception as e:
        console.print(f"\n[bold red]Authorization Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@publish_app.command(name="accounts")
def publish_accounts_cmd():
    """List connected publishing accounts and channels."""
    print_header(subtitle="Connected Publishing Accounts")
    db = Database()
    accounts = db.get_all_publishing_accounts(active_only=True)

    if not accounts:
        console.print(
            "[yellow]No connected accounts found. "
            "Run 'loopstash publish auth youtube' to connect.[/yellow]"
        )
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Channel Title", style="cyan bold")
    table.add_column("Channel ID", style="bold")
    table.add_column("Platform", style="dim")
    table.add_column("Connected At", style="dim")

    for acc in accounts:
        table.add_row(
            acc.channel_title, acc.channel_id, acc.platform.upper(), (acc.created_at or "")[:10]
        )

    console.print(table)


@publish_app.command(name="disconnect")
def publish_disconnect_cmd(
    platform: str = typer.Argument("youtube", help="Platform name."),
    account_id: Optional[str] = typer.Option(
        None, "--account", "-a", help="Specific channel ID to disconnect."
    ),
):
    """Disconnect an authorized publishing channel and remove stored credentials."""
    db = Database()
    auth_mgr = YouTubeAuthManager(db)
    target_id = account_id
    if not target_id:
        accounts = db.get_all_publishing_accounts(active_only=True)
        if not accounts:
            console.print("[yellow]No active channels to disconnect.[/yellow]")
            return
        target_id = accounts[0].channel_id

    ok = auth_mgr.disconnect_account(target_id)
    if ok:
        console.print(f"[green]Successfully disconnected channel '{target_id}'.[/green]")
    else:
        console.print(f"[yellow]Channel '{target_id}' was not found.[/yellow]")


@publish_app.command(name="preview")
def publish_preview_cmd(
    media_id: Optional[str] = typer.Option(None, "--media-id", "-m", help="Specific media ID."),
    all_eligible: bool = typer.Option(False, "--all", "-a", help="Preview all eligible videos."),
):
    """Preview a publishing batch, title transformations, and rights confirmation status."""
    print_header(subtitle="Publishing Batch Preview")
    db = Database()
    queue = PublishingQueue(db)

    items_to_check = []
    if media_id:
        items_to_check = [media_id]
    elif all_eligible:
        items_to_check = [it.id for it in db.get_all_media_items(excluded=False, limit=1000)]
    else:
        console.print("Please specify a video with --media-id or use --all.")
        return

    summary = queue.preview_batch(items_to_check)

    table = Table(box=box.ROUNDED)
    table.add_column("Media ID", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Rights Confirmed", justify="center")
    table.add_column("Already Uploaded", justify="center")

    for it in summary["items"]:
        rights_badge = (
            "[green]YES[/green]" if it["rights_confirmed"] else "[red]NO (REQUIRED)[/red]"
        )
        pub_badge = (
            f"[dim]YES ({it['youtube_id']})[/dim]"
            if it["already_published"]
            else "[green]NO[/green]"
        )
        table.add_row(
            it["media_id"],
            it["title"][:40],
            f"{it['file_size'] / (1024 * 1024):.1f} MB",
            rights_badge,
            pub_badge,
        )

    console.print(table)
    console.print(f"\n[bold]Total Videos:[/bold] {summary['total_count']}")
    console.print(f"[bold]Total Size:[/bold] {summary['total_bytes'] / (1024 * 1024):.2f} MB")

    if summary["issues"]:
        console.print("\n[bold red]Issues Detected:[/bold red]")
        for iss in summary["issues"]:
            console.print(f"  • [yellow]{iss['media_id']}[/yellow]: {iss['error']}")


@publish_app.command(name="youtube")
def publish_youtube_cmd(
    media_id: Optional[str] = typer.Option(None, "--media-id", "-m", help="Single video ID."),
    all_videos: bool = typer.Option(False, "--all", "-a", help="Publish all eligible videos."),
    now: bool = typer.Option(False, "--now", help="Publish immediately with public visibility."),
    schedule: bool = typer.Option(False, "--schedule", help="Schedule videos using scheduler."),
    channel: Optional[str] = typer.Option(
        None, "--channel", "-c", help="Target YouTube channel ID."
    ),
    confirm_rights: bool = typer.Option(
        False, "--confirm-rights", help="Confirm ownership or republishing license."
    ),
):
    """Publish videos publicly to YouTube or initiate scheduling."""
    db = Database()
    accounts = db.get_all_publishing_accounts(active_only=True)
    if not accounts:
        console.print(
            "[bold red]Error:[/bold red] No YouTube accounts connected. "
            "Run 'loopstash publish auth youtube' first."
        )
        raise typer.Exit(code=1)

    target_channel = channel or accounts[0].channel_id

    # Gather items
    media_ids: list[str] = []
    if media_id:
        media_ids = [media_id]
    elif all_videos:
        media_ids = [it.id for it in db.get_all_media_items(excluded=False, limit=500)]
    else:
        console.print("[yellow]Please specify --media-id <ID> or --all to select videos.[/yellow]")
        return

    if not media_ids:
        console.print("[yellow]No eligible videos found to publish.[/yellow]")
        return

    if schedule:
        console.print(
            "[cyan]Switching to scheduler. Run 'loopstash schedule create' "
            "for full configuration.[/cyan]"
        )
        return

    if not now:
        console.print("To execute immediate public publishing, specify the --now flag.")
        return

    # Section 13 & 14 confirmation
    print_header(subtitle="YouTube Immediate Public Publishing")
    console.print(f"[bold]Destination Channel:[/bold] {target_channel}")
    console.print(f"[bold]Videos to Publish:[/bold] {len(media_ids)}")
    console.print("[bold]Target Visibility:[/bold] [bold green]PUBLIC[/bold green]")

    if not confirm_rights:
        console.print("\n[bold yellow]Copyright & Licensing Safeguard:[/bold yellow]")
        console.print(
            "Under Section 13, you must confirm that you own this content or have "
            "explicit permission/license to republish it to YouTube."
        )
        confirmed = Confirm.ask(
            "Do you confirm you have all necessary rights to republish this content?"
        )
        if not confirmed:
            console.print("[red]Publishing aborted: Rights confirmation is mandatory.[/red]")
            raise typer.Exit(code=1)
        confirm_rights = True

    queue = PublishingQueue(db)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Uploading to YouTube...", total=len(media_ids))

        def chunk_cb(mid: str, sent: int, total: int, spd: float):
            progress.update(
                task,
                description=(
                    f"[cyan]Uploading {mid}: {sent // 1024} KB / {total // 1024} KB "
                    f"({spd / 1024:.0f} KB/s)"
                ),
            )

        results = queue.publish_now(
            media_ids=media_ids,
            channel_id=target_channel,
            confirm_rights=confirm_rights,
            progress_callback=chunk_cb,
        )
        progress.update(task, completed=len(media_ids), description="[green]Batch finished!")

    console.print()
    table = Table(title="Upload Results", box=box.ROUNDED)
    table.add_column("Media ID", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("YouTube URL")

    for r in results:
        status_style = (
            "[green]PUBLISHED[/green]"
            if r["status"] == "PUBLISHED"
            else f"[red]{r['status']}[/red]"
        )
        url_text = r.get("youtube_video_url") or r.get("error", "-")
        table.add_row(r["media_id"], status_style, url_text)

    console.print(table)


@publish_app.command(name="history")
def publish_history_cmd(
    limit: int = typer.Option(25, "--limit", "-n", help="Max records to display."),
):
    """View recent YouTube publishing jobs and statuses."""
    print_header(subtitle="Publishing History")
    db = Database()
    jobs = db.get_all_publishing_jobs(limit=limit)

    if not jobs:
        console.print("[dim]No publishing history recorded yet.[/dim]")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Job ID", style="cyan")
    table.add_column("Media ID", style="bold")
    table.add_column("Status")
    table.add_column("YouTube URL")
    table.add_column("Updated At", style="dim")

    for j in jobs:
        st_color = {
            PublishStatus.PUBLISHED: "[green]PUBLISHED[/green]",
            PublishStatus.SCHEDULED: "[cyan]SCHEDULED[/cyan]",
            PublishStatus.FAILED: "[red]FAILED[/red]",
            PublishStatus.UPLOAD_INTERRUPTED: "[yellow]INTERRUPTED[/yellow]",
            PublishStatus.NEEDS_ATTENTION: "[bold red]NEEDS_ATTENTION[/bold red]",
        }.get(j.status, j.status.value)

        table.add_row(
            j.id,
            j.media_id,
            st_color,
            j.youtube_video_url or (j.last_error[:30] if j.last_error else "-"),
            (j.updated_at or "")[:19],
        )

    console.print(table)


@publish_app.command(name="status")
def publish_status_cmd():
    """Display publishing quota capacity and overall publishing engine status."""
    print_header(subtitle="Publishing Engine Status")
    db = Database()
    qm = QuotaManager(db)
    q_status = qm.format_quota_status()

    table = Table(title="Daily Quota & API Usage (UTC)", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row("Date (UTC)", str(q_status["date_utc"]))
    table.add_row("Video Uploads Today", f"{q_status['uploads_used']} / {q_status['daily_limit']}")
    table.add_row(
        "Remaining Upload Capacity",
        f"[green]{q_status['remaining_capacity']}[/green]"
        if int(q_status["remaining_capacity"]) > 0
        else "[red]0 (Limit Reached)[/red]",
    )
    table.add_row("API Requests", str(q_status["api_requests"]))
    table.add_row("Last Request Time", str(q_status["last_request"])[:19])
    console.print(table)


# ============================================================================
# SCHEDULING COMMANDS
# ============================================================================


@schedule_app.command(name="preview")
def schedule_preview_cmd(
    start_date: str = typer.Option(..., "--start-date", "-d", help="Starting date (YYYY-MM-DD)."),
    start_time: str = typer.Option(..., "--start-time", "-t", help="Daily release time (HH:MM)."),
    timezone_str: str = typer.Option(
        "Asia/Kolkata", "--timezone", "-z", help="Display timezone (e.g. 'Asia/Kolkata', 'UTC')."
    ),
    interval: str = typer.Option(
        "daily", "--interval", "-i", help="Interval (daily, every_2d, weekly)."
    ),
    count: int = typer.Option(10, "--count", "-n", help="Number of publication slots to preview."),
):
    """Generate and preview publication calendar slots before scheduling."""
    print_header(subtitle="Publication Calendar Preview")
    db = Database()
    scheduler = YouTubeScheduler(db)

    try:
        slots = scheduler.preview_schedule(
            start_date=start_date,
            start_time=start_time,
            timezone_str=timezone_str,
            count=count,
            interval=interval,
        )
    except Exception as e:
        console.print(f"[bold red]Schedule Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    table = Table(box=box.ROUNDED)
    table.add_column("Slot", style="cyan", justify="right")
    table.add_column("Local Display Time", style="bold")
    table.add_column("UTC ISO8601 (status.publishAt)")

    for s in slots[:30]:
        table.add_row(str(s["index"]), s["local_display"], s["utc_iso"])

    console.print(table)
    if len(slots) > 30:
        console.print(f"[dim]... and {len(slots) - 30} more publication slots.[/dim]")


@schedule_app.command(name="create")
def schedule_create_cmd(
    start_date: str = typer.Option(..., "--start-date", "-d", help="Starting date (YYYY-MM-DD)."),
    start_time: str = typer.Option(
        ..., "--start-time", "-t", help="Daily publication time (HH:MM)."
    ),
    timezone_str: str = typer.Option(
        "Asia/Kolkata", "--timezone", "-z", help="Explicit timezone (e.g. 'Asia/Kolkata', 'UTC')."
    ),
    interval: str = typer.Option(
        "daily", "--interval", "-i", help="Interval (daily, every_2d, weekly)."
    ),
    channel: Optional[str] = typer.Option(
        None, "--channel", "-c", help="Target YouTube channel ID."
    ),
    media_id: Optional[str] = typer.Option(
        None, "--media-id", "-m", help="Single video ID to schedule."
    ),
    all_videos: bool = typer.Option(
        False, "--all", "-a", help="Schedule all eligible library videos."
    ),
    confirm_rights: bool = typer.Option(
        False, "--confirm-rights", help="Confirm ownership or republishing license."
    ),
):
    """Bulk upload videos ahead of time and schedule future public release."""
    db = Database()
    accounts = db.get_all_publishing_accounts(active_only=True)
    if not accounts:
        console.print(
            "[bold red]Error:[/bold red] No YouTube accounts connected. "
            "Run 'loopstash publish auth youtube' first."
        )
        raise typer.Exit(code=1)

    target_channel = channel or accounts[0].channel_id

    media_ids: list[str] = []
    if media_id:
        media_ids = [media_id]
    elif all_videos:
        media_ids = [it.id for it in db.get_all_media_items(excluded=False, limit=500)]
    else:
        console.print(
            "[yellow]Specify --media-id <ID> or --all to select videos for scheduling.[/yellow]"
        )
        return

    if not media_ids:
        console.print("[yellow]No eligible videos found to schedule.[/yellow]")
        return

    print_header(subtitle="Creating Bulk YouTube Publication Schedule")
    console.print(f"[bold]Channel:[/bold] {target_channel}")
    console.print(f"[bold]Videos to Schedule:[/bold] {len(media_ids)}")
    console.print(f"[bold]Start Date & Time:[/bold] {start_date} at {start_time} ({timezone_str})")
    console.print(f"[bold]Interval:[/bold] {interval}")

    if not confirm_rights:
        console.print("\n[bold yellow]Copyright & Licensing Safeguard:[/bold yellow]")
        confirmed = Confirm.ask(
            "Do you confirm you have all necessary rights to schedule and publish this content?"
        )
        if not confirmed:
            console.print("[red]Scheduling aborted: Rights confirmation is mandatory.[/red]")
            raise typer.Exit(code=1)
        confirm_rights = True

    scheduler = YouTubeScheduler(db)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task(
            "[cyan]Uploading and scheduling on YouTube...", total=len(media_ids)
        )

        def chunk_cb(mid: str, sent: int, total: int, spd: float):
            progress.update(
                task,
                description=(
                    f"[cyan]Uploading {mid}: {sent // 1024} KB / {total // 1024} KB "
                    f"({spd / 1024:.0f} KB/s)"
                ),
            )

        try:
            results = scheduler.schedule_batch(
                media_ids=media_ids,
                channel_id=target_channel,
                start_date=start_date,
                start_time=start_time,
                timezone_str=timezone_str,
                interval=interval,
                confirm_rights=confirm_rights,
                progress_callback=chunk_cb,
            )
            progress.update(task, completed=len(media_ids), description="[green]Done!")
        except Exception as e:
            console.print(f"\n[bold red]Scheduling Execution Error:[/bold red] {e}")
            raise typer.Exit(code=1) from e

    console.print()
    table = Table(title="Scheduled Publication Slots", box=box.ROUNDED)
    table.add_column("Media ID", style="cyan")
    table.add_column("Scheduled Public Release", style="bold")
    table.add_column("Status")
    table.add_column("YouTube URL")

    for r in results:
        status_style = (
            "[green]SCHEDULED[/green]"
            if r["status"] == "SCHEDULED"
            else f"[red]{r['status']}[/red]"
        )
        table.add_row(
            r["media_id"],
            r.get("local_display", "-"),
            status_style,
            r.get("youtube_video_url") or r.get("error", "-"),
        )

    console.print(table)


@schedule_app.command(name="list")
def schedule_list_cmd(
    channel: Optional[str] = typer.Option(None, "--channel", "-c", help="Filter by channel ID."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max schedule entries to show."),
):
    """List pending scheduled publications and calendar slots."""
    print_header(subtitle="Scheduled Publications Calendar")
    db = Database()
    entries = db.get_all_schedules(channel_id=channel, limit=limit)

    if not entries:
        console.print("[dim]No scheduled publication slots found.[/dim]")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Slot ID", style="cyan")
    table.add_column("Media ID", style="bold")
    table.add_column("Scheduled Local Time", style="bold")
    table.add_column("UTC Release Time")
    table.add_column("Status")

    for e in entries:
        st_style = (
            "[green]SCHEDULED[/green]"
            if e.status == PublishStatus.SCHEDULED
            else f"[dim]{e.status.value}[/dim]"
        )
        table.add_row(e.id, e.media_id, e.local_display_time, e.scheduled_utc, st_style)

    console.print(table)


@schedule_app.command(name="reschedule")
def schedule_reschedule_cmd(
    video_id: str = typer.Argument(..., help="YouTube video ID or Media ID to reschedule."),
    date: str = typer.Option(..., "--date", "-d", help="New publication date (YYYY-MM-DD)."),
    time_str: str = typer.Option(..., "--time", "-t", help="New publication time (HH:MM)."),
    timezone_str: str = typer.Option(
        "Asia/Kolkata", "--timezone", "-z", help="Explicit timezone name."
    ),
):
    """Update publication time for a scheduled YouTube video."""
    db = Database()
    scheduler = YouTubeScheduler(db)
    try:
        ok = scheduler.reschedule_video(
            job_or_media_id=video_id,
            new_date=date,
            new_time=time_str,
            timezone_str=timezone_str,
        )
        if ok:
            console.print(
                f"[green]Successfully rescheduled '{video_id}' to "
                f"{date} {time_str} ({timezone_str}).[/green]"
            )
    except Exception as e:
        console.print(f"[bold red]Rescheduling Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@schedule_app.command(name="cancel")
def schedule_cancel_cmd(
    video_id: str = typer.Argument(..., help="YouTube video ID or Media ID to cancel."),
):
    """Cancel a pending publication slot."""
    db = Database()
    scheduler = YouTubeScheduler(db)
    ok = scheduler.cancel_schedule(video_id)
    if ok:
        console.print(f"[green]Successfully cancelled scheduled slot for '{video_id}'.[/green]")
    else:
        console.print(f"[yellow]No scheduled slot found for '{video_id}'.[/yellow]")


# ============================================================================
# DIAGNOSTICS & CONFIG
# ============================================================================


@app.command(name="doctor")
def doctor():
    """Run comprehensive system, dependency, and environment diagnostic checks."""
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
            "\n[bold green]All systems nominal. LOOPSTASH is ready to operate![/bold green]"
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

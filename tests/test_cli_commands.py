"""Comprehensive CLI integration tests for LOOPSTASH commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from loopstash.cli import app
from loopstash.database import Database
from loopstash.models import MediaItem

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path):
    """Provide isolated database and storage paths for CLI tests."""
    db_file = tmp_path / "cli_test.db"
    db = Database(db_file)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    with (
        patch("loopstash.cli.Database", return_value=db),
        patch("loopstash.cli.StorageManager") as mock_sm,
    ):
        mock_sm.return_value.storage_dir = archive_dir
        yield {"db": db, "archive_dir": archive_dir}


def test_cli_help():
    """Verify root --help output and branding tagline."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "LOOPSTASH" in result.output
    assert "Collect. Preserve. Organise." in result.output


def test_cli_version():
    """Verify --version flag output."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "LOOPSTASH v" in result.output


def test_cli_doctor(cli_env):
    """Verify diagnostic doctor command executes and inspects environment."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Diagnostic Suite" in result.output or "DIAGNOSTIC" in result.output.upper()


def test_cli_config():
    """Verify config display and key updating."""
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "Configuration" in result.output

    # Test setting a config key
    result_set = runner.invoke(app, ["config", "--set", "max_retries=10"])
    assert result_set.exit_code == 0
    assert "max_retries" in result_set.output

    # Reset config
    result_reset = runner.invoke(app, ["config", "--reset"])
    assert result_reset.exit_code == 0


def test_cli_archive_subcommands(cli_env):
    """Verify archive subcommands: --help, status, profiles."""
    res_help = runner.invoke(app, ["archive", "--help"])
    assert res_help.exit_code == 0

    res_status = runner.invoke(app, ["archive", "status"])
    assert res_status.exit_code == 0

    res_profiles = runner.invoke(app, ["archive", "profiles"])
    assert res_profiles.exit_code == 0


def test_cli_library_commands(cli_env):
    """Verify library subcommands: list, search, inspect, export."""
    db: Database = cli_env["db"]

    # Insert a test media item
    item = MediaItem(
        id="med_cli_01",
        file_path="/tmp/fake_video.mp4",
        file_size=1024,
        title="CLI Test Video",
        caption="Exploring terminal apps #cli #python",
        source_id="CLITest01",
    )
    db.upsert_media_item(item)

    # 1. library list
    res_list = runner.invoke(app, ["library", "list"])
    assert res_list.exit_code == 0
    assert "med_cli_01" in res_list.output or "CLI Test Video" in res_list.output

    # 2. library search
    res_search = runner.invoke(app, ["library", "search", "terminal"])
    assert res_search.exit_code == 0
    assert "med_cli_01" in res_search.output or "CLI Test Video" in res_search.output

    # 3. library inspect
    res_inspect = runner.invoke(app, ["library", "inspect", "med_cli_01"])
    assert res_inspect.exit_code == 0
    assert "med_cli_01" in res_inspect.output

    # 4. library export JSON
    export_file = cli_env["archive_dir"] / "export.json"
    res_export = runner.invoke(
        app, ["library", "export", "--format", "json", "--output", str(export_file)]
    )
    assert res_export.exit_code == 0
    assert export_file.exists()


def test_cli_publish_subcommands(cli_env):
    """Verify publishing account and quota/status subcommands."""
    # publish help
    res_help = runner.invoke(app, ["publish", "--help"])
    assert res_help.exit_code == 0

    # publish status
    res_status = runner.invoke(app, ["publish", "status"])
    assert res_status.exit_code == 0
    assert "Daily Quota & API Usage" in res_status.output

    # publish accounts
    res_accounts = runner.invoke(app, ["publish", "accounts"])
    assert res_accounts.exit_code == 0


def test_cli_schedule_subcommands(cli_env):
    """Verify schedule preview and listing commands."""
    from datetime import datetime, timedelta, timezone

    future_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    res_preview = runner.invoke(
        app,
        [
            "schedule",
            "preview",
            "--start-date",
            future_date,
            "--start-time",
            "10:00",
            "--timezone",
            "UTC",
            "--count",
            "3",
            "--interval",
            "daily",
        ],
    )
    assert res_preview.exit_code == 0
    assert "Publication Calendar Preview" in res_preview.output
    assert future_date in res_preview.output

    # schedule list
    res_list = runner.invoke(app, ["schedule", "list"])
    assert res_list.exit_code == 0


def test_cli_new_required_commands(cli_env, tmp_path: Path):
    """Verify Section 22 required commands: publish import, retry, verify,
    schedule edit, and library exclude.
    """
    db: Database = cli_env["db"]

    # 1. library exclude
    item = MediaItem(id="med_cli_exclude", title="To Exclude")
    db.upsert_media_item(item)
    res_exclude = runner.invoke(app, ["library", "exclude", "med_cli_exclude"])
    assert res_exclude.exit_code == 0
    assert "excluded from publishing" in res_exclude.output

    # Undo exclude
    res_undo = runner.invoke(app, ["library", "exclude", "med_cli_exclude", "--undo"])
    assert res_undo.exit_code == 0
    assert "re-included" in res_undo.output

    # 2. publish import
    imp_dir = tmp_path / "cli_import_dir"
    imp_dir.mkdir()
    (imp_dir / "clip.mp4").write_bytes(b"CLIP_DATA")
    res_import = runner.invoke(app, ["publish", "import", str(imp_dir)])
    assert res_import.exit_code == 0
    assert "Added 1 new videos" in res_import.output

    # 3. publish retry
    res_retry = runner.invoke(app, ["publish", "retry"])
    assert res_retry.exit_code == 0
    assert "Retrying Publishing Jobs" in res_retry.output

    # 4. publish verify
    res_verify = runner.invoke(app, ["publish", "verify"])
    assert res_verify.exit_code == 0

    # 5. schedule edit --help
    res_sched_edit = runner.invoke(app, ["schedule", "edit", "--help"])
    assert res_sched_edit.exit_code == 0
    assert "Edit publication time" in res_sched_edit.output

    # 6. publish preview default without arguments
    res_preview = runner.invoke(app, ["publish", "preview"])
    assert res_preview.exit_code == 0
    assert "Publishing Batch Preview" in res_preview.output

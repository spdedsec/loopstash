"""Configuration management for LOOPSTASH."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from loopstash.utils.paths import get_default_config_dir, get_default_download_dir


@dataclass
class AppConfig:
    """LOOPSTASH application settings."""

    download_dir: str = field(default_factory=lambda: str(get_default_download_dir()))
    max_retries: int = 5
    browser: Optional[str] = None
    rate_limit_pause: float = 2.0
    min_free_space_mb: int = 500
    backend: str = "gallery-dl"
    verbose: bool = False
    no_color: bool = False

    def validate(self) -> None:
        """Validate configuration constraints."""
        if self.max_retries < 0 or self.max_retries > 50:
            raise ValueError("max_retries must be between 0 and 50.")
        if self.rate_limit_pause < 0:
            raise ValueError("rate_limit_pause must be non-negative.")
        if self.min_free_space_mb < 10:
            raise ValueError("min_free_space_mb must be at least 10 MB.")
        if self.browser:
            allowed = {
                "firefox",
                "chrome",
                "chromium",
                "brave",
                "edge",
                "opera",
                "vivaldi",
                "safari",
            }
            if self.browser.lower() not in allowed:
                raise ValueError(
                    f"Unsupported browser '{self.browser}'. Supported: {', '.join(sorted(allowed))}"
                )


def get_config_file_path() -> Path:
    """Return default config file path."""
    return get_default_config_dir() / "config.json"


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration from JSON file or return defaults."""
    cfg_path = path or get_config_file_path()
    if not cfg_path.is_file():
        cfg = AppConfig()
        # Save default config file on first run
        save_config(cfg, cfg_path)
        return cfg

    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = AppConfig(
            download_dir=data.get("download_dir", str(get_default_download_dir())),
            max_retries=int(data.get("max_retries", 5)),
            browser=data.get("browser"),
            rate_limit_pause=float(data.get("rate_limit_pause", 2.0)),
            min_free_space_mb=int(data.get("min_free_space_mb", 500)),
            backend=str(data.get("backend", "gallery-dl")),
            verbose=bool(data.get("verbose", False)),
            no_color=bool(data.get("no_color", False)),
        )
        cfg.validate()
        return cfg
    except Exception:
        # Fall back to defaults on corrupt config
        return AppConfig()


def save_config(config: AppConfig, path: Optional[Path] = None) -> None:
    """Persist configuration to JSON file."""
    config.validate()
    cfg_path = path or get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(config), f, indent=2)


def update_config_key(key: str, value: Any, path: Optional[Path] = None) -> AppConfig:
    """Update a single configuration key."""
    cfg = load_config(path)
    if not hasattr(cfg, key):
        raise KeyError(f"Unknown configuration key: {key}")

    current_val = getattr(cfg, key)
    if isinstance(value, str) and value.strip().lower() in ("none", "null", ""):
        value = None
    elif isinstance(current_val, int):
        value = int(value)
    elif isinstance(current_val, float):
        value = float(value)
    elif isinstance(current_val, bool):
        if isinstance(value, str):
            value = value.lower() in ("true", "1", "yes")

    setattr(cfg, key, value)
    cfg.validate()
    save_config(cfg, path)
    return cfg


def reset_config(path: Optional[Path] = None) -> AppConfig:
    """Reset configuration to factory defaults."""
    cfg = AppConfig()
    save_config(cfg, path)
    return cfg

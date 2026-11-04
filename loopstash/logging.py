"""Logging setup with sensitive data redaction for LOOPSTASH."""

from __future__ import annotations

import logging
import re

from rich.logging import RichHandler

_COOKIE_PATTERN = re.compile(r"(sessionid|ds_user_id|csrftoken|mid)=([^;\s&]+)", re.IGNORECASE)


class RedactingFilter(logging.Filter):
    """Filter out any accidental cookie or session credential values in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _COOKIE_PATTERN.sub(r"\1=[REDACTED]", record.msg)
        if record.args:
            cleaned_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    cleaned_args.append(_COOKIE_PATTERN.sub(r"\1=[REDACTED]", arg))
                else:
                    cleaned_args.append(arg)
            record.args = tuple(cleaned_args)
        return True


def setup_logging(verbose: bool = False, no_color: bool = False) -> None:
    """Configure application-wide logging."""
    level = logging.DEBUG if verbose else logging.INFO

    handler = RichHandler(
        level=level,
        show_time=verbose,
        show_path=verbose,
        markup=True,
        rich_tracebacks=True,
    )
    handler.addFilter(RedactingFilter())

    root_logger = logging.getLogger("loopstash")
    root_logger.setLevel(level)
    root_logger.handlers = [handler]
    root_logger.propagate = False

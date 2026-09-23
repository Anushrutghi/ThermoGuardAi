"""Central logging configuration."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from backend.core.config import get_settings

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_FORMAT_DETAIL = "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s"


def setup_logging(level: str | None = None) -> None:
    """Configure root logger with console + rotating file handlers."""
    settings = get_settings()
    log_level = (level or settings.log_level).upper()

    root = logging.getLogger()
    root.setLevel(log_level)

    # Avoid duplicate handlers on hot reload
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    log_dir = settings.root_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "thermoguard.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT_DETAIL))
    root.addHandler(file_handler)

    # Quiet noisy third-party loggers
    for noisy in ("uvicorn.access", "matplotlib", "PIL", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a module-level logger (configures logging on first use)."""
    if not logging.getLogger().handlers:
        setup_logging()
    return logging.getLogger(name)

"""Logging configuration with secret redaction handlers."""

import logging
import sys
from pathlib import Path

from natras.config.settings import Settings
from natras.logging.redaction import (
    GLOBAL_SECRET_REGISTRY,
    SecretRedactingFilter,
    SecretRedactingFormatter,
)

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    settings: Settings | None = None,
    log_file: Path | str | None = None,
    level: str = "INFO",
) -> logging.Logger:
    """Configure system logging with automated secret redaction."""
    if settings:
        GLOBAL_SECRET_REGISTRY.register_many(settings.get_secrets())
        level = settings.log_level

    log_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("natras")
    logger.setLevel(log_level)

    # Avoid duplicate handlers on re-setup
    if logger.handlers:
        logger.handlers.clear()

    formatter = SecretRedactingFormatter(DEFAULT_LOG_FORMAT, datefmt=DATE_FORMAT)
    redaction_filter = SecretRedactingFilter()

    # Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redaction_filter)
    logger.addHandler(console_handler)

    # Optional File Handler
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redaction_filter)
        logger.addHandler(file_handler)

    return logger

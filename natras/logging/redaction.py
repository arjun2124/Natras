"""Secret redaction filter and formatter for logging."""

import logging
import re
from typing import Iterable, Set

REDACTED_REPLACEMENT = "[REDACTED]"

# Patterns matching sensitive token assignments and bearer tokens
SENSITIVE_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE),
    re.compile(r"((?:token|secret|password|api_key|access_token|key)\s*[:=]\s*['\"]?)[^\s,'\"&]+(['\"]?)", re.IGNORECASE),
    re.compile(r"ey[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]+"),  # JWT format
]


class SecretRegistry:
    """Thread-safe registry for known secret strings to redact."""

    def __init__(self) -> None:
        self._secrets: Set[str] = set()

    def register(self, secret: str) -> None:
        if secret and len(secret.strip()) >= 3:
            self._secrets.add(secret.strip())

    def register_many(self, secrets: Iterable[str]) -> None:
        for s in secrets:
            self.register(s)

    def get_secrets(self) -> Set[str]:
        return set(self._secrets)


GLOBAL_SECRET_REGISTRY = SecretRegistry()


def redact_string(text: str, custom_secrets: Set[str] | None = None) -> str:
    """Scrub known secrets and sensitive patterns from text."""
    if not text:
        return text

    scrubbed = text

    # Scrub exact registered secrets
    secrets_to_check = GLOBAL_SECRET_REGISTRY.get_secrets()
    if custom_secrets:
        secrets_to_check.update(custom_secrets)

    for secret in secrets_to_check:
        if secret in scrubbed:
            scrubbed = scrubbed.replace(secret, REDACTED_REPLACEMENT)

    # Scrub regex patterns
    scrubbed = SENSITIVE_PATTERNS[0].sub(rf"\g<1>{REDACTED_REPLACEMENT}", scrubbed)
    scrubbed = SENSITIVE_PATTERNS[1].sub(rf"\g<1>{REDACTED_REPLACEMENT}\g<2>", scrubbed)
    scrubbed = SENSITIVE_PATTERNS[2].sub(REDACTED_REPLACEMENT, scrubbed)

    return scrubbed


class SecretRedactingFilter(logging.Filter):
    """Logging filter that scrubs secrets from record msg and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Safely merge args first to avoid breaking format string tokens like %s
            if record.args:
                record.msg = record.getMessage()
                record.args = ()
            if isinstance(record.msg, str):
                record.msg = redact_string(record.msg)
        except Exception:
            pass
        return True


class SecretRedactingFormatter(logging.Formatter):
    """Logging formatter that scrubs secrets from the final formatted log string."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_string(formatted)

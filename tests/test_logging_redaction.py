"""Unit tests for automated secret redaction in logging."""

import io
import logging
import pytest

from natras.config.settings import Settings
from natras.logging.redaction import (
    GLOBAL_SECRET_REGISTRY,
    redact_string,
)
from natras.logging.setup import setup_logging


def test_redact_exact_registered_secret():
    test_secret = "fyers_raw_secret_xyz123987"
    GLOBAL_SECRET_REGISTRY.register(test_secret)

    sample_text = f"Connecting to broker with key={test_secret} and done."
    redacted = redact_string(sample_text)
    assert test_secret not in redacted
    assert "[REDACTED]" in redacted


def test_redact_bearer_token():
    text = "Authorization: Bearer my_super_long_secret_token_12345"
    redacted = redact_string(text)
    assert "my_super_long_secret_token_12345" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_redact_jwt_pattern():
    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    text = f"Received token: {fake_jwt}"
    redacted = redact_string(text)
    assert fake_jwt not in redacted
    assert "[REDACTED]" in redacted


def test_redact_token_key_value_syntax():
    text = "Error detail: api_key=secret99887766 and password='super_secret_password!'"
    redacted = redact_string(text)
    assert "secret99887766" not in redacted
    assert "super_secret_password!" not in redacted


def test_setup_logging_scrubs_output():
    secret_val = "upstox_private_token_9999"
    settings = Settings(upstox_access_token=secret_val)
    logger = setup_logging(settings)

    # Capture output
    stream = io.StringIO()
    handler = logger.handlers[0]
    original_stream = handler.stream
    handler.stream = stream

    try:
        logger.info("Attempting login with token: %s", secret_val)
        output = stream.getvalue()
        assert secret_val not in output
        assert "[REDACTED]" in output
    finally:
        handler.stream = original_stream

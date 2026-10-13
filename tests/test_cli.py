"""Unit tests for CLI diagnostic commands."""

from unittest.mock import patch
import pytest

from natras.cli.main import main, print_status, run_verify


def test_cli_print_status(capsys):
    print_status()
    captured = capsys.readouterr()
    assert "NAT RAS SYSTEM STATUS" in captured.out
    assert "PAPER ONLY (Fail-Closed)" in captured.out
    assert "v2.2.0-RESEARCH-MOMENTUM-WICK" in captured.out
    assert "[ACTIVE_PAPER]" in captured.out


def test_cli_run_verify(capsys):
    code = run_verify()
    assert code == 0
    captured = capsys.readouterr()
    assert "ALL SYSTEM SAFETY DIAGNOSTICS PASSED" in captured.out


def test_cli_main_dispatch(capsys):
    with patch("sys.argv", ["natras", "status"]):
        main()
    captured = capsys.readouterr()
    assert "NAT RAS SYSTEM STATUS" in captured.out

"""Unit tests for Telegram bot adapter and command dispatcher."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from natras.adapters.telegram_live import TelegramLiveBot
from natras.config.constants import MARKET_TIMEZONE_STR, OrderIntent, OrderSide, OrderType
from natras.db.models import PositionRecord


def test_telegram_send_message_with_redaction(monkeypatch):
    secret_token = "SECRET_TOKEN_99999"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret_token)
    monkeypatch.setenv("TELEGRAM_AUTHORIZED_USER_ID", "123456789")

    bot = TelegramLiveBot()
    assert bot.enabled is True
    bot._secrets = {secret_token}

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.post", return_value=mock_resp) as mock_post:
        ok = bot.send_message(f"Alert with secret: {secret_token}")
        assert ok is True
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert secret_token not in payload["text"]
        assert "[REDACTED]" in payload["text"]


def test_telegram_commands_dispatch(db):
    bot = TelegramLiveBot(token="mock_tok", chat_id="12345", db_manager=db)

    # Test /help
    help_resp = bot.handle_command("/help")
    assert "/status" in help_resp
    assert "/paper_status" in help_resp

    # Test /status
    status_resp = bot.handle_command("/status")
    assert "NAT RAS SYSTEM STATUS" in status_resp
    assert "PAPER ONLY" in status_resp

    # Test /auth_status
    auth_resp = bot.handle_command("/auth_status")
    assert "AUTHENTICATION STATUS" in auth_resp

    # Test /strategy_status
    strat_resp = bot.handle_command("/strategy_status")
    assert "v2.2.0-RESEARCH-MOMENTUM-WICK" in strat_resp

    # Test /paper_status (empty)
    paper_resp = bot.handle_command("/paper_status")
    assert "PAPER TRADING STATUS" in paper_resp
    assert "Open Positions: 0" in paper_resp

    # Test /open_positions (empty)
    pos_resp = bot.handle_command("/open_positions")
    assert "No active positions" in pos_resp

    # Test /today_report
    rep_resp = bot.handle_command("/today_report")
    assert "TODAY'S TRADING REPORT" in rep_resp

    # Test /shadow_status
    shadow_resp = bot.handle_command("/shadow_status")
    assert "SHADOW STRATEGIES" in shadow_resp

    # Test /promotion_requests
    promo_resp = bot.handle_command("/promotion_requests")
    assert "PROMOTION REQUESTS" in promo_resp

    # Test /approve without argument
    appr_resp = bot.handle_command("/approve")
    assert "Usage: `/approve <candidate_id>`" in appr_resp

    # Test /reject without argument
    rej_resp = bot.handle_command("/reject")
    assert "Usage: `/reject <candidate_id>`" in rej_resp

    # Test unknown
    unk_resp = bot.handle_command("/random_cmd")
    assert "Unknown command" in unk_resp


def test_telegram_open_positions_with_active_position(db):
    bot = TelegramLiveBot(token="mock_tok", chat_id="12345", db_manager=db)
    pos = PositionRecord(
        position_id="pos_test_1",
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350CE",
        side=OrderSide.BUY.value,
        quantity=65,
        entry_price=105.5,
        current_price=112.0,
        stop_loss=90.5,
        take_profit=None,
    )
    db.save_position(pos)

    resp = bot.handle_command("/open_positions")
    assert "NSE:NIFTY26O1317350CE" in resp
    assert "Qty: 65" in resp
    assert "105.50" in resp


def test_telegram_poll_once_filters_unauthorized_user(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock_bot_token")
    monkeypatch.setenv("TELEGRAM_AUTHORIZED_USER_ID", "1001")

    bot = TelegramLiveBot()
    assert bot.enabled is True

    # 1 authorized message from 1001, 1 unauthorized from 9999
    mock_updates = {
        "ok": True,
        "result": [
            {
                "update_id": 1,
                "message": {"chat": {"id": 9999}, "text": "/status"},
            },
            {
                "update_id": 2,
                "message": {"chat": {"id": 1001}, "text": "/status"},
            },
        ],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_updates

    with patch("requests.get", return_value=mock_resp), \
         patch.object(bot, "send_message", return_value=True) as mock_send:
        handled = bot.poll_once()
        assert handled == 1
        mock_send.assert_called_once()
        assert "NAT RAS SYSTEM STATUS" in mock_send.call_args[0][0]

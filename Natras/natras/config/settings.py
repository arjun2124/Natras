"""Configuration settings and environment loading with fail-closed safety."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set
from zoneinfo import ZoneInfo

from natras.config.constants import (
    MARKET_TIMEZONE_STR,
    STRATEGY_FAMILY,
)


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""
    pass


class LiveTradingProhibitedError(ConfigurationError):
    """Raised when live trading is attempted or configured."""
    pass


def _parse_env_file(filepath: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file without external dependencies."""
    env_vars: dict[str, str] = {}
    if not filepath.exists():
        return env_vars

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                env_vars[key] = val
    return env_vars


@dataclass(frozen=True)
class Settings:
    """System-wide configuration settings with fail-closed safety."""

    # Core Safety
    live_orders_enabled: bool = False
    system_env: str = "development"
    log_level: str = "INFO"

    # Strategy & Timezone
    strategy_family: str = STRATEGY_FAMILY
    timezone_name: str = MARKET_TIMEZONE_STR

    # Database
    database_path: Path = Path("data/natras.db")

    # Risk Governor
    max_daily_loss: float = 5000.0
    max_position_size: int = 100
    max_open_positions: int = 2
    max_daily_trades: int = 10

    # Broker Credentials (Never exposed in repr or logs)
    fyers_app_id: str = ""
    fyers_secret_key: str = ""
    fyers_redirect_uri: str = "https://127.0.0.1:5000/callback"
    fyers_access_token: str = ""

    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "https://127.0.0.1:5000/callback"
    upstox_access_token: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Sensitive fields tracking
    _secret_field_names: Set[str] = field(
        default_factory=lambda: {
            "fyers_secret_key",
            "fyers_access_token",
            "upstox_api_key",
            "upstox_api_secret",
            "upstox_access_token",
            "telegram_bot_token",
        },
        repr=False,
    )

    def __post_init__(self) -> None:
        """Enforce non-negotiable safety rules at initialization."""
        # Rule: LIVE_ORDERS_ENABLED must strictly remain false
        if self.live_orders_enabled:
            raise LiveTradingProhibitedError(
                "FAIL-CLOSED SAFETY TRIGGERED: live_orders_enabled is True. "
                "NAT RAS is strictly paper-trading only. Live orders are prohibited."
            )

        # Rule: Single strategy family lock
        if self.strategy_family != STRATEGY_FAMILY:
            raise ConfigurationError(
                f"Unauthorized strategy family: {self.strategy_family}. "
                f"Only '{STRATEGY_FAMILY}' is permitted."
            )

    @property
    def market_tz(self) -> ZoneInfo:
        """Returns the market ZoneInfo."""
        return ZoneInfo(self.timezone_name)

    def get_secrets(self) -> set[str]:
        """Collect non-empty secret values for logging redaction."""
        secrets: set[str] = set()
        for field_name in self._secret_field_names:
            val = getattr(self, field_name, None)
            if val and isinstance(val, str) and len(val.strip()) > 3:
                secrets.add(val.strip())
        return secrets

    def __repr__(self) -> str:
        """Safe string representation redacting all credentials."""
        return (
            f"Settings("
            f"live_orders_enabled={self.live_orders_enabled}, "
            f"system_env='{self.system_env}', "
            f"strategy_family='{self.strategy_family}', "
            f"timezone='{self.timezone_name}', "
            f"database_path='{self.database_path}', "
            f"max_daily_loss={self.max_daily_loss}, "
            f"max_position_size={self.max_position_size}, "
            f"max_open_positions={self.max_open_positions}, "
            f"credentials='[PROTECTED]')"
        )


def load_settings(env_file: Path | str | None = None) -> Settings:
    """Load settings from environment variables and optional .env file."""
    env_vars: dict[str, str] = {}

    if env_file:
        env_vars.update(_parse_env_file(Path(env_file)))
    else:
        # Default check for .env in current directory or workspace root
        default_env = Path(".env")
        if default_env.exists():
            env_vars.update(_parse_env_file(default_env))

    # Environment variables take precedence over .env file
    for key, val in os.environ.items():
        env_vars[key] = val

    # Safety check on raw input before parsing
    raw_live = env_vars.get("LIVE_ORDERS_ENABLED", "false").strip().lower()
    if raw_live in ("true", "1", "yes", "enabled"):
        raise LiveTradingProhibitedError(
            "FAIL-CLOSED: LIVE_ORDERS_ENABLED cannot be set to true. "
            "Live trading is strictly prohibited."
        )

    live_orders_enabled = False

    return Settings(
        live_orders_enabled=live_orders_enabled,
        system_env=env_vars.get("SYSTEM_ENV", "development"),
        log_level=env_vars.get("LOG_LEVEL", "INFO").upper(),
        strategy_family=env_vars.get("STRATEGY_FAMILY", STRATEGY_FAMILY),
        timezone_name=env_vars.get("MARKET_TIMEZONE", MARKET_TIMEZONE_STR),
        database_path=Path(env_vars.get("DATABASE_PATH", "data/natras.db")),
        max_daily_loss=float(env_vars.get("MAX_DAILY_LOSS", 5000.0)),
        max_position_size=int(env_vars.get("MAX_POSITION_SIZE", 100)),
        max_open_positions=int(env_vars.get("MAX_OPEN_POSITIONS", 2)),
        max_daily_trades=int(env_vars.get("MAX_DAILY_TRADES", 10)),
        fyers_app_id=env_vars.get("FYERS_APP_ID", ""),
        fyers_secret_key=env_vars.get("FYERS_SECRET_KEY", ""),
        fyers_redirect_uri=env_vars.get("FYERS_REDIRECT_URI", "https://127.0.0.1:5000/callback"),
        fyers_access_token=env_vars.get("FYERS_ACCESS_TOKEN", ""),
        upstox_api_key=env_vars.get("UPSTOX_API_KEY", ""),
        upstox_api_secret=env_vars.get("UPSTOX_API_SECRET", ""),
        upstox_redirect_uri=env_vars.get("UPSTOX_REDIRECT_URI", "https://127.0.0.1:5000/callback"),
        upstox_access_token=env_vars.get("UPSTOX_ACCESS_TOKEN", ""),
        telegram_bot_token=env_vars.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env_vars.get("TELEGRAM_CHAT_ID", ""),
    )

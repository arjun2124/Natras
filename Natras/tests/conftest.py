"""Pytest fixtures for NAT RAS testing."""

import pytest
from natras.config.settings import Settings
from natras.db.database import DatabaseManager
from natras.execution.paper_executor import PaperExecutor
from natras.risk.governor import RiskGovernor, RiskLimits
from natras.safety.guards import SafetyGuard


@pytest.fixture
def settings() -> Settings:
    """Fixture providing default paper-only settings."""
    return Settings(
        live_orders_enabled=False,
        database_path=":memory:",
        max_daily_loss=5000.0,
        max_position_size=100,
        max_open_positions=2,
        max_daily_trades=10,
    )


@pytest.fixture
def db() -> DatabaseManager:
    """Fixture providing in-memory SQLite database manager."""
    return DatabaseManager(":memory:")


@pytest.fixture
def safety_guard(settings: Settings) -> SafetyGuard:
    """Fixture providing safety guard."""
    return SafetyGuard(settings)


@pytest.fixture
def risk_governor(settings: Settings) -> RiskGovernor:
    """Fixture providing risk governor."""
    return RiskGovernor(settings=settings)


@pytest.fixture
def paper_executor(
    db: DatabaseManager,
    safety_guard: SafetyGuard,
    risk_governor: RiskGovernor,
    settings: Settings,
) -> PaperExecutor:
    """Fixture providing fully initialized paper executor."""
    return PaperExecutor(
        db=db,
        safety_guard=safety_guard,
        risk_governor=risk_governor,
        settings=settings,
    )

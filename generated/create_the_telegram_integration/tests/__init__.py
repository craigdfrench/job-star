# Test package for the Telegram intake bot.


// --- DUPLICATE BLOCK ---

"""Pytest configuration for telegram_bot tests."""
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"

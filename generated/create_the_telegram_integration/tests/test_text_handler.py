"""Unit tests for the text intake handler."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.telegram_bot.config import BotConfig
from services.telegram_bot.intake_queue import IntakeQueue
from services.telegram_bot.models import IntakeSource, IntakeStatus
from services.telegram_bot.text_handler import TextIntakeHandler


def _config() -> BotConfig:
    return BotConfig(
        bot_token="dummy:token",
        authorized_user_ids={123},
    )


def _fixed_now() -> datetime:
    return datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def queue() -> IntakeQueue:
    return IntakeQueue()


@pytest.fixture
def handler(queue: IntakeQueue) -> TextIntakeHandler:
    return TextIntakeHandler(config=_config(), queue=queue, now=_fixed_now)


def test_basic_text_accepted(handler: TextIntakeHandler, queue: IntakeQueue) -> None:
    result = handler.handle_text(
        user_id=123,
        chat_id=123,
        message_id=1,
        text="Follow up with recruiter about the Stripe role",
    )
    assert result.accepted is True
    assert result.item is not None
    assert result.item.source == IntakeSource.TEXT
    assert result.item.status == IntakeStatus.NEW
    assert result.item.user_id == 123
    assert result.item.created_at == _fixed_now()
    assert "✅ Captured" in result.confirmation
    assert "Follow up" in result.confirmation
    assert queue.size() == 1
    assert queue.peek().id == "tg-text-123-1"


def test_empty_message_rejected(handler: TextIntakeHandler, queue: IntakeQueue) -> None:
    result = handler.handle_text(user_id=123, chat_id=123, message_id=2, text="   ")
    assert result.accepted is False
    assert result.item is None
    assert "Empty" in result.confirmation
    assert queue.size() == 0


def test_whitespace_and_zero_width_only_rejected(handler: TextIntakeHandler, queue: IntakeQueue) -> None:
    result = handler.handle_text(
        user_id=123, chat_id=123, message_id=3, text="\u200b\u200c   \n\t"
    )
    assert result.accepted is False
    assert queue.size() == 0


def test_punctuation_only_rejected(handler: TextIntakeHandler, queue: IntakeQueue) -> None:
    # No alphanumeric characters -> not meaningful.
    result = handler.handle_text(user_id=123, chat_id=123, message_id=4, text="... !!! ???")
    assert result.accepted is False
    assert queue.size() == 0


def test_multiline_formatting_preserved(handler: TextIntakeHandler, queue: IntakeQueue) -> None:
    text = "Line one\nLine two\n  - bullet\n  - bullet"
    result = handler.handle_text(user_id=123, chat_id=123, message_id=5, text=text)
    assert result.accepted is True
    assert result.item is not None
    assert result.item.raw_text == text  # internal newlines preserved


def test_long_message_accepted_with_warning(handler: TextIntakeHandler, queue: IntakeQueue) -> None:
    long_text = "a" * 2500
    result = handler.handle_text(user_id=123, chat_id=123, message_id=6, text=long_text)
    assert result.accepted is True
    assert result.warning is not None
    assert "Long message" in result.warning
    assert "ℹ️" in result.confirmation
    assert len(result.item.raw_text) == 2500  # not truncated


def test_confirmation_preview_truncated(handler: TextIntakeHandler) -> None:
    text = "x" * 500
    result = handler.handle_text(user_id=123, chat_id=123, message_id=7, text=text)
    assert result.accepted is True
    assert "…" in result.confirmation
    # Preview should be single-line.
    assert "\n" not in result.confirmation.split("ℹ️")[0]


def test_id_is_stable(handler: TextIntakeHandler) -> None:
    r1 = handler.handle_text(user_id=123, chat_id=123, message_id=10, text="hi")
    r2 = handler.handle_text(user_id=123, chat_id=123, message_id=10, text="hi again")
    assert r1.item.id == r2.item.id == "tg-text-123-10"


def test_username_stored(handler: TextIntakeHandler) -> None:
    result = handler.handle_text(
        user_id=123, chat_id=123, message_id=11, text="note", username="alice"
    )
    assert result.item.username == "alice"


def test_raw_payload_stored_in_meta(handler: TextIntakeHandler) -> None:
    raw = {"message_id": 12, "text": "hi"}
    result = handler.handle_text(
        user_id=123, chat_id=123, message_id=12, text="hi", raw=raw
    )
    assert result.item.meta["raw"] == raw

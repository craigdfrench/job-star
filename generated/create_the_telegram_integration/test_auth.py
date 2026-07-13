"""Tests for the AuthGuard authorization middleware."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.telegram_bot.auth import (
    AuthGuard,
    UnauthorizedAttempt,
    auth_middleware,
    build_authorized_handler,
)


def _make_update(user_id=111, username="alice", text="hello", chat_id=222):
    """Build a minimal fake Update object for testing."""
    user = SimpleNamespace(
        id=user_id, username=username, first_name="Alice"
    )
    chat = SimpleNamespace(id=chat_id)
    message = SimpleNamespace(text=text, voice=None)
    return SimpleNamespace(
        effective_user=user,
        effective_chat=chat,
        message=message,
        update_id=1,
    )


def _make_context():
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


# ---------------------------------------------------------------------- #
# is_authorized
# ---------------------------------------------------------------------- #
class TestIsAuthorized:
    def test_authorized_user_returns_true(self):
        guard = AuthGuard(allowed_user_ids=[111, 222])
        update = _make_update(user_id=111)
        assert guard.is_authorized(update) is True

    def test_unauthorized_user_returns_false(self):
        guard = AuthGuard(allowed_user_ids=[111])
        update = _make_update(user_id=999)
        assert guard.is_authorized(update) is False

    def test_empty_allowlist_rejects_everyone(self):
        """Fail-closed: no allowlist means nobody is allowed."""
        guard = AuthGuard(allowed_user_ids=[])
        update = _make_update(user_id=111)
        assert guard.is_authorized(update) is False

    def test_no_user_context_rejected(self):
        guard = AuthGuard(allowed_user_ids=[111])
        update = SimpleNamespace(
            effective_user=None,
            effective_chat=SimpleNamespace(id=1),
            message=None,
            update_id=2,
        )
        assert guard.is_authorized(update) is False

    def test_allowed_user_ids_is_immutable(self):
        guard = AuthGuard(allowed_user_ids=[1, 2, 3])
        assert guard.allowed_user_ids == frozenset({1, 2, 3})
        # frozenset — can't mutate
        with pytest.raises(AttributeError):
            guard.allowed_user_ids.add(4)


# ---------------------------------------------------------------------- #
# extract_attempt / log_unauthorized
# ---------------------------------------------------------------------- #
class TestExtractAndLog:
    def test_extract_attempt_captures_fields(self):
        guard = AuthGuard(allowed_user_ids=[])
        update = _make_update(user_id=999, username="snoop", text="secret stuff here")
        attempt = guard.extract_attempt(update)
        assert isinstance(attempt, UnauthorizedAttempt)
        assert attempt.user_id == 999
        assert attempt.username == "snoop"
        assert attempt.chat_id == 222
        assert "secret stuff here" in attempt.message_preview
        assert attempt.timestamp  # non-empty ISO string

    def test_extract_attempt_truncates_long_text(self):
        guard = AuthGuard(allowed_user_ids=[])
        long_text = "x" * 500
        update = _make_update(text=long_text)
        attempt = guard.extract_attempt(update)
        assert len(attempt.message_preview) <= 80

    def test_extract_attempt_handles_voice(self):
        guard = AuthGuard(allowed_user_ids=[])
        update = _make_update(text="")
        update.message.text = None
        update.message.voice = SimpleNamespace(duration=12)
        attempt = guard.extract_attempt(update)
        assert "voice" in attempt.message_preview
        assert "12" in attempt.message_preview

    def test_log_unauthorized_emits_warning(self, caplog):
        guard = AuthGuard(allowed_user_ids=[])
        attempt = UnauthorizedAttempt(
            user_id=999,
            username="snoop",
            first_name="Snoop",
            chat_id=222,
            timestamp="2024-01-01T00:00:00Z",
            message_preview="hi",
        )
        with caplog.at_level(logging.WARNING, logger="services.telegram_bot.auth"):
            guard.log_unauthorized(attempt)
        assert any("Unauthorized access attempt" in r.message for r in caplog.records)
        assert any("user_id=999" in r.message for r in caplog.records)


# ---------------------------------------------------------------------- #
# auth_middleware
# ---------------------------------------------------------------------- #
class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_authorized_proceeds(self):
        guard = AuthGuard(allowed_user_ids=[111])
        update = _make_update(user_id=111)
        ctx = _make_context()
        result = await auth_middleware(guard, update, ctx)
        assert result is True
        ctx.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_unauthorized_rejected_and_notified(self):
        guard = AuthGuard(allowed_user_ids=[111])
        update = _make_update(user_id=999)
        ctx = _make_context()
        result = await auth_middleware(guard, update, ctx)
        assert result is False
        ctx.bot.send_message.assert_awaited_once()
        # Message should mention "not authorized"
        sent_text = ctx.bot.send_message.call_args.kwargs["text"]
        assert "not authorized" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_unauthorized_no_chat_does_not_crash(self):
        guard = AuthGuard(allowed_user_ids=[111])
        update = _make_update(user_id=999)
        update.effective_chat = None
        ctx = _make_context()
        # Should not raise even without a chat to reply to.
        result = await auth_middleware(guard, update, ctx)
        assert result is False
        ctx.bot.send_message.assert_not_called()


# ---------------------------------------------------------------------- #
# build_authorized_handler
# ---------------------------------------------------------------------- #
class TestBuildAuthorizedHandler:
    @pytest.mark.asyncio
    async def test_authorized_calls_inner_handler(self):
        guard = AuthGuard(allowed_user_ids=[111])
        inner = AsyncMock()
        wrapped = build_authorized_handler(guard, inner)
        update = _make_update(user_id=111)
        ctx = _make_context()
        await wrapped(update, ctx)
        inner.assert_awaited_once_with(update, ctx)

    @pytest.mark.asyncio
    async def test_unauthorized_skips_inner_handler(self):
        guard = AuthGuard(allowed_user_ids=[111])
        inner = AsyncMock()
        wrapped = build_authorized_handler(guard, inner)
        update = _make_update(user_id=999)
        ctx = _make_context()
        await wrapped(update, ctx)
        inner.assert_not_awaited()

    def test_wrapped_preserves_name(self):
        guard = AuthGuard(allowed_user_ids=[])
        async def my_handler(update, context):
            pass
        wrapped = build_authorized_handler(guard, my_handler)
        assert wrapped.__name__ == "authorized_my_handler"

"""Unit tests for QQ channel (no real botpy WebSocket connection)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from app.channels.bus import MessageBus, OutboundMessage
from app.channels.qq import QQChannel


@pytest_asyncio.fixture
def bus():
    return MessageBus()


@pytest_asyncio.fixture
def channel(bus):
    ch = QQChannel(bus, app_id="fake-app-id", secret="fake-secret")
    ch._bot = Mock()
    ch._bot.api.post_c2c_message = AsyncMock()
    return ch


def _make_fake_message(openid: str = "test-openid", content: str = "测试消息", msg_id: str = "msg-001"):
    """Build a minimal fake botpy C2C message."""
    return SimpleNamespace(
        author=SimpleNamespace(id=openid),
        content=content,
        id=msg_id,
    )


class TestQQChannelBasics:
    def test_has_token_true(self, channel):
        assert channel.has_token is True

    def test_has_token_false_no_app_id(self, bus):
        ch = QQChannel(bus, app_id="", secret="s")
        assert ch.has_token is False

    def test_has_token_false_no_secret(self, bus):
        ch = QQChannel(bus, app_id="a", secret="")
        assert ch.has_token is False


class TestQQChannelOnMessage:
    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, channel, bus):
        msg = _make_fake_message(content="帮我搜索一下")
        await channel._on_message(msg, is_group=False)

        inbound = await bus.consume_inbound()
        assert inbound.content == "帮我搜索一下"
        assert inbound.channel == "qq"
        assert inbound.sender_id == "test-openid"

    @pytest.mark.asyncio
    async def test_sends_ack_before_processing(self, channel, bus):
        msg = _make_fake_message()
        await channel._on_message(msg, is_group=False)

        channel._bot.api.post_c2c_message.assert_called_once()
        call_args = channel._bot.api.post_c2c_message.call_args
        assert "处理中" in str(call_args.kwargs.get("content", ""))

    @pytest.mark.asyncio
    async def test_deduplicates_same_msg_id(self, channel, bus):
        msg1 = _make_fake_message(msg_id="dup-001", content="第一条")
        msg2 = _make_fake_message(msg_id="dup-001", content="第一条")

        await channel._on_message(msg1, is_group=False)
        await channel._on_message(msg2, is_group=False)

        await bus.consume_inbound()
        assert bus.inbound.empty()

    @pytest.mark.asyncio
    async def test_allows_different_msg_ids(self, channel, bus):
        msg1 = _make_fake_message(msg_id="id-1", content="第一条")
        msg2 = _make_fake_message(msg_id="id-2", content="第二条")

        await channel._on_message(msg1, is_group=False)
        await channel._on_message(msg2, is_group=False)

        m1 = await bus.consume_inbound()
        m2 = await bus.consume_inbound()
        assert m1.content == "第一条"
        assert m2.content == "第二条"

    @pytest.mark.asyncio
    async def test_skips_empty_content(self, channel, bus):
        msg = _make_fake_message(content="")
        await channel._on_message(msg, is_group=False)

        assert bus.inbound.empty()
        channel._bot.api.post_c2c_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_message_without_id(self, channel, bus):
        msg = _make_fake_message(msg_id=None, content="无ID消息")
        await channel._on_message(msg, is_group=False)

        inbound = await bus.consume_inbound()
        assert inbound.content == "无ID消息"


class TestQQChannelSend:
    @pytest.mark.asyncio
    async def test_sends_c2c_text(self, channel):
        msg = OutboundMessage(channel="qq", chat_id="user-123", content="回复内容")
        await channel.send(msg)

        channel._bot.api.post_c2c_message.assert_called_once()
        call_kwargs = channel._bot.api.post_c2c_message.call_args.kwargs
        assert call_kwargs["openid"] == "user-123"
        assert call_kwargs["content"] == "回复内容"
        assert call_kwargs["msg_type"] == 0

    @pytest.mark.asyncio
    async def test_send_skips_when_bot_not_connected(self, bus):
        ch = QQChannel(bus, app_id="a", secret="s")
        msg = OutboundMessage(channel="qq", chat_id="x", content="x")
        await ch.send(msg)  # should not raise

    @pytest.mark.asyncio
    async def test_send_skips_empty_openid(self, channel):
        msg = OutboundMessage(channel="qq", chat_id="", content="x")
        await channel.send(msg)
        channel._bot.api.post_c2c_message.assert_not_called()

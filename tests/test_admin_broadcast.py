import asyncio

import bot


def test_broadcast_button_url_validation():
    assert bot.valid_broadcast_button_url("https://example.org/download?source=bot")
    assert bot.valid_broadcast_button_url("http://t.me/example")
    assert not bot.valid_broadcast_button_url("javascript:alert(1)")
    assert not bot.valid_broadcast_button_url("https://user:secret@example.org")
    assert not bot.valid_broadcast_button_url("https://example.org:99999")
    assert not bot.valid_broadcast_button_url("https://example.org/a b")


def test_broadcast_sends_text_optional_button_photo_and_document_to_users_and_channel(monkeypatch):
    calls = []

    class FakeBot:
        def __init__(self, fail_channel=False):
            self.fail_channel = fail_channel

        def _record(self, kind, kwargs):
            if self.fail_channel and kwargs.get("chat_id") == bot.GLOBAL_BROADCAST_CHANNEL:
                raise RuntimeError("bot is not allowed to post")
            calls.append((kind, kwargs))

        async def send_message(self, **kwargs):
            self._record("message", kwargs)

        async def send_photo(self, **kwargs):
            self._record("photo", kwargs)

        async def send_document(self, **kwargs):
            self._record("document", kwargs)

    monkeypatch.setattr(bot, "registered_user_ids", lambda: [101])
    telegram_bot = FakeBot()

    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        "Anuncio de prueba <seguro> & texto",
        button_label="Abrir enlace",
        button_url="https://example.org/app",
    ))
    assert result == (1, 1, 0, True)
    assert [kind for kind, _ in calls] == ["message", "message"]
    by_chat = {kwargs["chat_id"]: kwargs for _, kwargs in calls}
    for chat_id in (101, bot.GLOBAL_BROADCAST_CHANNEL):
        message = by_chat[chat_id]
        assert message["parse_mode"] is None
        assert "Anuncio de prueba <seguro> & texto" in message["text"]
        assert message["reply_markup"].inline_keyboard[0][0].url == "https://example.org/app"
    calls.clear()

    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        "Anuncio con foto",
        media_type="photo",
        media_file_id="telegram-photo-file-id",
    ))
    assert result == (1, 1, 0, True)
    assert [kind for kind, _ in calls] == ["photo", "photo"]
    assert {kwargs["chat_id"] for _, kwargs in calls} == {101, bot.GLOBAL_BROADCAST_CHANNEL}
    assert all(kwargs["photo"] == "telegram-photo-file-id" for _, kwargs in calls)
    assert all("Anuncio con foto" in kwargs["caption"] for _, kwargs in calls)
    assert all(kwargs["parse_mode"] is None for _, kwargs in calls)
    calls.clear()

    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        "Anuncio con APK",
        media_type="document",
        media_file_id="telegram-apk-file-id",
    ))
    assert result == (1, 1, 0, True)
    assert [kind for kind, _ in calls] == ["document", "document"]
    assert {kwargs["chat_id"] for _, kwargs in calls} == {101, bot.GLOBAL_BROADCAST_CHANNEL}
    assert all(kwargs["document"] == "telegram-apk-file-id" for _, kwargs in calls)
    assert all("Anuncio con APK" in kwargs["caption"] for _, kwargs in calls)
    assert all(kwargs["parse_mode"] is None for _, kwargs in calls)
    calls.clear()

    long_body = "x" * 1100
    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        long_body,
        media_type="photo",
        media_file_id="telegram-photo-file-id",
    ))
    assert result == (1, 1, 0, True)
    assert [kind for kind, _ in calls] == ["message", "photo", "message", "photo"]
    assert [kwargs["chat_id"] for _, kwargs in calls] == [
        101, 101, bot.GLOBAL_BROADCAST_CHANNEL, bot.GLOBAL_BROADCAST_CHANNEL
    ]
    assert long_body in calls[0][1]["text"]
    assert calls[1][1]["photo"] == "telegram-photo-file-id"
    assert long_body in calls[2][1]["text"]
    assert calls[3][1]["photo"] == "telegram-photo-file-id"


def test_channel_permission_failure_does_not_mark_user_delivery_as_failed(monkeypatch):
    calls = []

    class FakeBot:
        async def send_message(self, **kwargs):
            if kwargs["chat_id"] == bot.GLOBAL_BROADCAST_CHANNEL:
                raise RuntimeError("bot is not an admin")
            calls.append(kwargs)

    monkeypatch.setattr(bot, "registered_user_ids", lambda: [101])
    result = asyncio.run(bot.broadcast_admin_message(FakeBot(), "Prueba"))

    assert result == (1, 1, 0, False)
    assert len(calls) == 1
    assert calls[0]["chat_id"] == 101

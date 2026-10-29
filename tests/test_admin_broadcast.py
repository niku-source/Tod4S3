import asyncio

import bot


def test_broadcast_button_url_validation():
    assert bot.valid_broadcast_button_url("https://example.org/download?source=bot")
    assert bot.valid_broadcast_button_url("http://t.me/example")
    assert not bot.valid_broadcast_button_url("javascript:alert(1)")
    assert not bot.valid_broadcast_button_url("https://user:secret@example.org")
    assert not bot.valid_broadcast_button_url("https://example.org:99999")
    assert not bot.valid_broadcast_button_url("https://example.org/a b")


def test_broadcast_sends_optional_button_photo_and_document(monkeypatch):
    calls = []

    class FakeBot:
        async def send_message(self, **kwargs):
            calls.append(("message", kwargs))

        async def send_photo(self, **kwargs):
            calls.append(("photo", kwargs))

        async def send_document(self, **kwargs):
            calls.append(("document", kwargs))

    monkeypatch.setattr(bot, "registered_user_ids", lambda: [101])
    telegram_bot = FakeBot()

    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        "Anuncio de prueba <seguro> & texto",
        button_label="Abrir enlace",
        button_url="https://example.org/app",
    ))
    assert result == (1, 1, 0)
    assert len(calls) == 1
    kind, message = calls.pop()
    assert kind == "message"
    assert message["parse_mode"] is None
    assert "Anuncio de prueba <seguro> & texto" in message["text"]
    assert message["reply_markup"].inline_keyboard[0][0].url == "https://example.org/app"

    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        "Anuncio con foto",
        media_type="photo",
        media_file_id="telegram-photo-file-id",
    ))
    assert result == (1, 1, 0)
    assert [kind for kind, _ in calls] == ["photo"]
    assert calls[0][1]["photo"] == "telegram-photo-file-id"
    assert "Anuncio con foto" in calls[0][1]["caption"]
    assert calls[0][1]["parse_mode"] is None
    calls.clear()

    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        "Anuncio con APK",
        media_type="document",
        media_file_id="telegram-apk-file-id",
    ))
    assert result == (1, 1, 0)
    assert [kind for kind, _ in calls] == ["document"]
    assert calls[0][1]["document"] == "telegram-apk-file-id"
    assert "Anuncio con APK" in calls[0][1]["caption"]
    assert calls[0][1]["parse_mode"] is None
    calls.clear()

    long_body = "x" * 1100
    result = asyncio.run(bot.broadcast_admin_message(
        telegram_bot,
        long_body,
        media_type="photo",
        media_file_id="telegram-photo-file-id",
    ))
    assert result == (1, 1, 0)
    assert [kind for kind, _ in calls] == ["message", "photo"]
    assert long_body in calls[0][1]["text"]
    assert calls[1][1]["photo"] == "telegram-photo-file-id"

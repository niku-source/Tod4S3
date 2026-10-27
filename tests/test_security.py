import asyncio
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from bot import (
    HIDDEN_OWNER_ID,
    MIB,
    clean_filename,
    download_keyboard,
    download_menu_keyboard,
    download_menu_text,
    download_progress_text,
    download_telegram_media,
    extract_http_urls,
    help_page_text,
    main_keyboard,
    post_init,
    post_shutdown,
    sc,
    split_into_parts,
    start_command,
    task_keyboard,
    transfer_complete_text,
    upload_progress_text,
    upload_to_todus_s3_with_progress,
    validate_url_syntax,
    result_keyboard,
    card,
)
from task_queue import FairTaskQueue, TransferTask
from todus_s3_storage import (
    ToDusS3UploadCancelled,
    ToDusS3UploadError,
    build_object_key,
    upload_to_todus_s3,
)

GIB = 1024**3


class TelegramCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_replies_with_welcome_and_keyboard(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456, full_name="Ana Pérez"),
            effective_message=message,
        )
        member_bot = SimpleNamespace(get_chat_member=AsyncMock(
            side_effect=[SimpleNamespace(status="member"), SimpleNamespace(status="member"),
                         SimpleNamespace(status="member")]
        ))
        await start_command(update, SimpleNamespace(bot=member_bot))

        message.reply_text.assert_awaited_once()
        args, kwargs = message.reply_text.call_args
        self.assertIn("TodusS3ʙᴏᴛꫂꤪꤨᴼᶠᶜ", args[0])
        self.assertIn("ʜᴏʟᴀ, ᴀɴᴀ", args[0])
        self.assertIn("ᴇɴᴠɪ́ᴀ", args[0])
        self.assertIn("ᴀ ʟᴀ ɴᴜʙᴇ", args[0])
        self.assertIn("ᴘᴇɢᴀ ᴜɴ ᴇɴʟᴀᴄᴇ", args[0])
        self.assertGreater(len(args[0].splitlines()), 8)
        self.assertIsNotNone(kwargs.get("reply_markup"))
        self.assertIn("├──────────────────────┤", args[0])

    async def test_start_requires_all_three_chats_before_welcome(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456, full_name="Ana Pérez"),
            effective_message=message,
        )
        member_bot = SimpleNamespace(get_chat_member=AsyncMock(
            side_effect=[SimpleNamespace(status="left"), SimpleNamespace(status="left"),
                         SimpleNamespace(status="left")]
        ))
        await start_command(update, SimpleNamespace(bot=member_bot))

        message.reply_text.assert_awaited_once()
        args, kwargs = message.reply_text.call_args
        self.assertNotIn("TodusS3ʙᴏᴛꫂꤪꤨᴼᶠᶜ", args[0])
        self.assertIn("@Dark_Zone_666", args[0])
        self.assertIn("@grupozonaoscura", args[0])
        self.assertIn("@darzPrbo", args[0])
        buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertEqual(
            [button.url for button in buttons if button.url],
            ["https://t.me/Dark_Zone_666", "https://t.me/grupozonaoscura", "https://t.me/darzPrbo"],
        )
        self.assertEqual(member_bot.get_chat_member.await_count, 3)

    async def test_start_gate_also_applies_to_owner(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=HIDDEN_OWNER_ID, full_name="Niku Blade"),
            effective_message=message,
        )
        owner_bot = SimpleNamespace(get_chat_member=AsyncMock(
            side_effect=[SimpleNamespace(status="left"), SimpleNamespace(status="member"),
                         SimpleNamespace(status="member")]
        ))
        await start_command(update, SimpleNamespace(bot=owner_bot))

        args, kwargs = message.reply_text.call_args
        self.assertNotIn("TodusS3ʙᴏᴛꫂꤪꤨᴼᶠᶜ", args[0])
        buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertTrue(any(button.callback_data == "membership:verify" for button in buttons))

    def test_main_keyboard_has_balance_and_plan_button(self):
        buttons = [button for row in main_keyboard().inline_keyboard for button in row]
        labels = [button.text for button in buttons]
        self.assertEqual(len(labels), 8)
        self.assertIn("📥 DESCARGAS", labels)
        self.assertIn("💰 SALDO: 0$ · REFERIDOS", labels)
        self.assertIn("❓ AYUDA", labels)
        self.assertIn("📒 HISTORIAL", labels)
        self.assertIn("📋 COLA", labels)
        self.assertIn("💬 SOPORTE", labels)
        self.assertIn("📊 MI CUOTA", labels)
        self.assertIn("🏆 TOP USUARIOS", labels)
        self.assertFalse(any(button.url for button in buttons))
        self.assertTrue(all(button.style for button in buttons))

    def test_download_menu_offers_telegram_and_direct_url_routes(self):
        buttons = [button for row in download_menu_keyboard().inline_keyboard for button in row]
        callbacks = [button.callback_data for button in buttons]
        self.assertEqual(callbacks[:2], ["download:telegram", "download:url"])
        self.assertIn(sc("🏠 inicio"), [button.text for button in buttons])
        text = download_menu_text()
        self.assertIn("ᴄᴇɴᴛʀᴏ ᴅᴇ ᴅᴇꜱᴄᴀʀɢᴀꜱ", text)
        self.assertIn("📁", text)
        self.assertIn("🔗", text)

    async def test_startup_without_mtproto_credentials_keeps_bot_available(self):
        application = SimpleNamespace(
            bot=SimpleNamespace(
                token="123:token",
                get_me=AsyncMock(return_value=SimpleNamespace(username="jk_bot")),
                set_my_commands=AsyncMock(),
            ),
            bot_data={},
        )
        with patch.dict(os.environ, {
            "TELEGRAM_API_ID": "", "TELEGRAM_API_HASH": "", "TELEGRAM_BACKUP_CHAT_ID": "",
        }):
            with self.assertLogs("jk-telegram-bot", level="INFO") as captured:
                await post_init(application)
        self.assertIsNone(application.bot_data["mtproto_client"])
        mtproto_records = [record for record in captured.records if "MTProto opcional" in record.getMessage()]
        self.assertEqual(len(mtproto_records), 1)
        self.assertEqual(mtproto_records[0].levelno, logging.INFO)

    async def test_startup_with_mtproto_credentials_starts_and_closes_client(self):
        client = SimpleNamespace(
            start=AsyncMock(),
            disconnect=AsyncMock(),
            is_connected=Mock(return_value=True),
            add_event_handler=Mock(),
            catch_up=AsyncMock(),
        )
        application = SimpleNamespace(
            bot=SimpleNamespace(
                token="123:token",
                get_me=AsyncMock(return_value=SimpleNamespace(username="jk_bot")),
                set_my_commands=AsyncMock(),
            ),
            bot_data={},
        )
        with patch.dict(
            os.environ,
            {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "test-hash", "TELEGRAM_BACKUP_CHAT_ID": ""},
        ), patch("bot.TelegramClient", return_value=client) as client_factory:
            await post_init(application)
            client_factory.assert_called_once()
            client.start.assert_awaited_once_with(bot_token="123:token")
            client.add_event_handler.assert_called_once()
            client.catch_up.assert_awaited_once()
            self.assertIs(application.bot_data["mtproto_client"], client)
            await post_shutdown(application)
            client.disconnect.assert_awaited_once()


class DownloadButtonTests(unittest.TestCase):
    def test_download_has_one_direct_button_with_full_url(self):
        url = "https://s3.todus.cu/stream/users/tg_1/telegram/file.bin"
        buttons = [button for row in download_keyboard(url).inline_keyboard for button in row]
        download_buttons = [button for button in buttons if button.url]
        self.assertEqual(len(download_buttons), 1)
        self.assertEqual(download_buttons[0].url, url)

    def test_result_card_does_not_print_raw_url(self):
        url = "https://s3.todus.cu/stream/users/tg_1/telegram/example.bin"
        text = transfer_complete_text("pelicula.mp4", 10 * MIB)
        self.assertNotIn(url, text)
        self.assertIn("ᴘᴇʟɪᴄᴜʟᴀ.ᴍᴘ4", text)
        self.assertIn("10.49 ᴍʙ", text)
        self.assertIn("ᴅᴜʀᴀᴄ", text)

    def test_card_renders_current_box_style(self):
        text = card("Archivo listo", ["A&B <clip>.mp4"])
        self.assertIn("╭━━━━━━━━━━━━━━━━━━━━━━╮", text)
        self.assertIn("ᴀʀᴄʜɪᴠᴏ ʟɪꜱᴛᴏ", text)

    def test_small_caps_preserves_links_commands_and_variable_names(self):
        rendered = sc("Agrega TELEGRAM_API_HASH, abre /ayuda: https://my.telegram.org")
        self.assertIn("TELEGRAM_API_HASH", rendered)
        self.assertIn("/ayuda", rendered)
        self.assertIn("https://my.telegram.org", rendered)

    def test_help_explains_history_and_api_setup(self):
        history_page = help_page_text(3)
        setup_page = help_page_text(1)
        self.assertIn("/historial", history_page)
        self.assertIn("/cola", history_page)
        self.assertIn("ᴅᴏᴄᴜᴍᴇɴᴛᴏꜱ", setup_page)


class TaskActionTests(unittest.TestCase):
    def test_active_upload_has_cancel_and_cancelled_upload_has_resume(self):
        job = TransferTask(1, 1, "url", None, None)
        job.state = "uploading"
        active_labels = [button.text for row in task_keyboard(job).inline_keyboard for button in row]
        self.assertEqual(active_labels, [sc("⏸️ pausar")])

        job.state = "cancelled"
        cancelled_labels = [button.text for row in task_keyboard(job).inline_keyboard for button in row]
        self.assertEqual(cancelled_labels[0], sc("▶️ reanudar"))

    def test_completed_single_or_multipart_task_has_one_download_action(self):
        job = TransferTask(1, 1, "url", None, None)
        job.state = "done"
        job.part_urls = {0: "https://s3.todus.cu/stream/users/tg_1/telegram/one.bin"}
        single = [button for row in task_keyboard(job).inline_keyboard for button in row]
        single_downloads = [button for button in single if button.url]
        self.assertEqual(len(single_downloads), 1)
        self.assertEqual(single_downloads[0].url, "https://s3.todus.cu/stream/users/tg_1/telegram/one.bin")

        job.part_urls = {
            0: "https://s3.todus.cu/stream/users/tg_1/telegram/one.bin",
            1: "https://s3.todus.cu/stream/users/tg_1/telegram/two.bin",
        }
        multipart = [button for row in task_keyboard(job).inline_keyboard for button in row]
        self.assertTrue(any(button.callback_data == f"j:{job.task_id}:links" for button in multipart))


class TransferResultTests(unittest.TestCase):
    def test_result_keyboard_has_one_download_action(self):
        markup = result_keyboard("https://example.com/movie.mp4", "task123")
        download_buttons = [button for row in markup.inline_keyboard for button in row if button.url]
        self.assertEqual([button.url for button in download_buttons], ["https://example.com/movie.mp4"])


class ForwardedMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_forward_uses_mtproto_message_and_downloads_without_bot_api(self):
        class Media:
            file_size = 3 * GIB
            file_name = "large.bin"

            get_file = AsyncMock(side_effect=AssertionError("no usar Bot API HTTP"))

        mtproto_message = SimpleNamespace(media=object())

        async def write_downloaded_media(message, file, progress_callback=None):
            Path(file).write_bytes(b"file")
            return file

        mtproto = SimpleNamespace(
            get_messages=AsyncMock(return_value=mtproto_message),
            download_media=AsyncMock(side_effect=write_downloaded_media),
        )

        message = SimpleNamespace(
            document=Media(), video=None, animation=None, audio=None,
            voice=None, video_note=None, photo=None, chat_id=1234, message_id=77,
        )
        path, filename, size = await download_telegram_media(message, mtproto)
        try:
            self.assertEqual(filename, "large.bin")
            self.assertEqual(size, 4)
            mtproto.get_messages.assert_awaited_once_with(1234, ids=77)
            mtproto.download_media.assert_awaited_once()
            message.document.get_file.assert_not_awaited()
        finally:
            path.unlink(missing_ok=True)

    async def test_large_forward_without_mtproto_returns_actionable_configuration_error(self):
        media = SimpleNamespace(file_size=3 * GIB, file_name="large.bin", get_file=AsyncMock())
        message = SimpleNamespace(
            document=media, video=None, animation=None, audio=None,
            voice=None, video_note=None, photo=None, chat_id=1234, message_id=77,
        )
        with self.assertRaisesRegex(ValueError, "Archivo muy grande"):
            await download_telegram_media(message)
        media.get_file.assert_not_awaited()

    async def test_large_forward_prefers_cached_mtproto_message(self):
        media = SimpleNamespace(file_size=3 * GIB, file_name="large.bin", get_file=AsyncMock())
        message = SimpleNamespace(
            document=media, video=None, animation=None, audio=None,
            voice=None, video_note=None, photo=None, chat_id=1234, message_id=77,
        )
        mtproto_message = SimpleNamespace(media=object())
        cache = {(1234, 77): mtproto_message}

        async def write_downloaded_media(message, file, progress_callback=None):
            Path(file).write_bytes(b"file")
            return file

        mtproto = SimpleNamespace(
            get_messages=AsyncMock(side_effect=AssertionError("el cache ya tiene el mensaje")),
            download_media=AsyncMock(side_effect=write_downloaded_media),
        )
        path, filename, size = await download_telegram_media(
            message, mtproto, mtproto_message_cache=cache
        )
        try:
            self.assertEqual(size, 4)
            mtproto.download_media.assert_awaited_once_with(
                mtproto_message, file=str(path), progress_callback=None
            )
            mtproto.get_messages.assert_not_awaited()
        finally:
            path.unlink(missing_ok=True)

    async def test_small_forward_still_uses_standard_bot_api_download(self):
        class TelegramFile:
            async def download_to_drive(self, custom_path):
                Path(custom_path).write_bytes(b"small")

        media = SimpleNamespace(
            file_size=5,
            file_name="small.bin",
            get_file=AsyncMock(return_value=TelegramFile()),
        )
        message = SimpleNamespace(
            document=media, video=None, animation=None, audio=None,
            voice=None, video_note=None, photo=None, chat_id=1234, message_id=78,
        )
        path, filename, size = await download_telegram_media(message)
        try:
            self.assertEqual(filename, "small.bin")
            self.assertEqual(size, 5)
            media.get_file.assert_awaited_once()
        finally:
            path.unlink(missing_ok=True)

    async def test_forwarded_apk_preserves_filename_and_validates_bytes(self):
        class TelegramFile:
            async def download_to_drive(self, custom_path):
                with ZipFile(custom_path, "w") as package:
                    package.writestr("AndroidManifest.xml", b"binary-manifest")
                    package.writestr("classes.dex", b"dex\n035\0")

        with TemporaryDirectory() as directory:
            probe = Path(directory) / "application.apk"
            with ZipFile(probe, "w") as package:
                package.writestr("AndroidManifest.xml", b"binary-manifest")
                package.writestr("classes.dex", b"dex\n035\0")
            size = probe.stat().st_size
        media = SimpleNamespace(
            file_size=size,
            file_name="application.apk",
            get_file=AsyncMock(return_value=TelegramFile()),
        )
        message = SimpleNamespace(
            document=media, video=None, animation=None, audio=None,
            voice=None, video_note=None, photo=None, chat_id=1234, message_id=79,
        )
        path, filename, actual_size = await download_telegram_media(message)
        try:
            self.assertEqual(filename, "application.apk")
            self.assertEqual(actual_size, size)
            with ZipFile(path) as package:
                self.assertIn("classes.dex", package.namelist())
        finally:
            path.unlink(missing_ok=True)


class URLValidationTests(unittest.TestCase):
    def test_accepts_public_https_url_syntax(self):
        value = "https://example.com/file.bin"
        self.assertEqual(validate_url_syntax(value), value)

    def test_rejects_ftp(self):
        with self.assertRaises(ValueError):
            validate_url_syntax("ftp://example.com/file")

    def test_rejects_credentials(self):
        with self.assertRaises(ValueError):
            validate_url_syntax("https://user:pass@example.com/file")

    def test_rejects_localhost(self):
        with self.assertRaises(ValueError):
            validate_url_syntax("http://localhost/file")

    def test_rejects_private_ip(self):
        with self.assertRaises(ValueError):
            validate_url_syntax("http://192.168.1.2/file")

    def test_accepts_nonstandard_public_port(self):
        self.assertEqual(validate_url_syntax("https://example.com:8443/file"), "https://example.com:8443/file")

    def test_extracts_single_url_from_forwarded_text(self):
        self.assertEqual(
            extract_http_urls("Mira este archivo: https://example.com/video.mp4, bájalo"),
            ["https://example.com/video.mp4"],
        )

    def test_extracts_multiple_urls_in_one_message(self):
        self.assertEqual(
            extract_http_urls("https://example.com/a https://example.com/b"),
            ["https://example.com/a", "https://example.com/b"],
        )

    def test_extracts_multiple_urls_for_queueing(self):
        self.assertEqual(
            extract_http_urls("https://example.com/a https://example.org/b"),
            ["https://example.com/a", "https://example.org/b"],
        )


class FairTaskQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_robin_selects_different_users_before_second_job(self):
        queue = FairTaskQueue()
        make_task = lambda user_id: TransferTask(user_id, user_id, "url", None, None)
        first, second, third = make_task(1), make_task(1), make_task(2)
        await queue.enqueue(first, 5)
        await queue.enqueue(second, 5)
        await queue.enqueue(third, 5)
        self.assertIs(await queue.take(), first)
        self.assertIs(await queue.take(), third)
        self.assertIs(await queue.take(), second)

    async def test_per_user_limit_rejects_excess_tasks(self):
        queue = FairTaskQueue()
        self.assertTrue(await queue.enqueue(TransferTask(1, 1, "url", None, None), 1))
        self.assertFalse(await queue.enqueue(TransferTask(1, 1, "url", None, None), 1))

    async def test_resume_preserves_completed_parts_after_cancellation(self):
        queue = FairTaskQueue()
        job = TransferTask(1, 1, "url", None, None)
        job.set_state("cancelled")
        job.cancel_requested = True
        job.part_urls[0] = "https://s3.todus.cu/stream/users/tg_1/telegram/part-one.bin"
        await queue.requeue(job)
        self.assertEqual(job.state, "queued")
        self.assertFalse(job.cancel_requested)
        self.assertEqual(
            job.part_urls,
            {0: "https://s3.todus.cu/stream/users/tg_1/telegram/part-one.bin"},
        )


class ToDusS3StorageTests(unittest.IsolatedAsyncioTestCase):
    def test_object_key_matches_telegram_user_namespace(self):
        key = build_object_key(7935796405)
        self.assertRegex(
            key,
            r"^users/tg_7935796405/telegram/\d{8}-\d{6}-[a-f0-9]{6}/telegram_[a-f0-9]{12}_download\.bin$",
        )
        with self.assertRaises(ValueError):
            build_object_key(0)

    async def test_upload_uses_public_put_without_authorization(self):
        class FakeResponse:
            status_code = 200

        class FakeSession:
            def __init__(self):
                self.request = None
                self.body = b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def put(self, url, **kwargs):
                self.request = (url, kwargs)
                self.body = b"".join(kwargs["data"])
                return FakeResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "clip.mp4"
            path.write_bytes(b"fake-video")
            fake_session = FakeSession()
            with patch("todus_s3_storage.requests.Session", return_value=fake_session):
                link, size = await asyncio.to_thread(
                    upload_to_todus_s3, path, path.name, 7935796405
                )

        self.assertTrue(link.startswith(
            "https://s3.todus.cu/stream/users/tg_7935796405/telegram/"
        ))
        self.assertRegex(link, r"/telegram_[a-f0-9]{12}_clip\.mp4$")
        self.assertEqual(size, len(b"fake-video"))
        self.assertEqual(fake_session.body, b"fake-video")
        headers = fake_session.request[1]["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertIn("clip.mp4", headers["Content-Disposition"])

    async def test_upload_rejects_server_error(self):
        class FakeResponse:
            status_code = 403

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def put(self, *_args, **_kwargs):
                return FakeResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "clip.mp4"
            path.write_bytes(b"fake-video")
            with patch("todus_s3_storage.requests.Session", return_value=FakeSession()):
                with self.assertRaisesRegex(ToDusS3UploadError, "HTTP 403"):
                    await asyncio.to_thread(upload_to_todus_s3, path, path.name, 12345)

    async def test_upload_cancellation_before_put(self):
        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def put(self, *_args, **_kwargs):
                raise AssertionError("No debe iniciar una subida cancelada")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "clip.mp4"
            path.write_bytes(b"fake-video")
            with patch("todus_s3_storage.requests.Session", return_value=FakeSession()):
                with self.assertRaises(ToDusS3UploadCancelled):
                    await asyncio.to_thread(
                        upload_to_todus_s3,
                        path,
                        path.name,
                        12345,
                        is_cancelled=lambda: True,
                    )

    async def test_upload_wrapper_reports_progress_and_returns_todus_link(self):
        status = SimpleNamespace(edit_text=AsyncMock())

        def fake_upload(_path, _filename, _user_id, **kwargs):
            kwargs["on_progress"](5, 10)
            return "https://s3.todus.cu/stream/users/tg_12345/telegram/test.bin", 10

        with patch("bot.upload_to_todus_s3", side_effect=fake_upload):
            result = await upload_to_todus_s3_with_progress(
                status, Path("clip.mp4"), "clip.mp4", 12345
            )
        self.assertEqual(
            result,
            ("https://s3.todus.cu/stream/users/tg_12345/telegram/test.bin", 10),
        )
        self.assertGreaterEqual(status.edit_text.await_count, 1)


class ProgressTests(unittest.TestCase):
    def test_progress_text_is_compact_and_has_percent_bar_without_repeated_signature(self):
        result = upload_progress_text(5 * MIB, 10 * MIB)
        self.assertIn("50%", result)
        self.assertIn("▓", result)
        self.assertNotIn("ᴍᴏᴅ ʙʏ", result)

    def test_download_progress_text_shows_mtproto_progress(self):
        result = download_progress_text(5 * MIB, 10 * MIB)
        self.assertIn("ᴅᴇꜱᴄᴀʀɢᴀɴᴅᴏ", result)
        self.assertIn("50%", result)
        self.assertIn("ᴍʙ/s", result)
        self.assertIn("ᴄᴀʟᴄᴜʟᴀɴᴅᴏ", result)


class FilenameTests(unittest.TestCase):
    def test_strips_path_and_dangerous_separators(self):
        self.assertEqual(clean_filename("../../bad:name.bin"), "bad_name.bin")

    def test_empty_filename_falls_back(self):
        self.assertEqual(clean_filename("../"), "download.bin")

    def test_removes_generated_telegram_prefix(self):
        from bot import normalize_telegram_generated_name
        self.assertEqual(
            normalize_telegram_generated_name("Telegram_MiAplicacion.apk", "application.apk"),
            "MiAplicacion.apk",
        )

    def test_split_into_parts_keeps_bytes_and_ordinal_names(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.bin"
            source.write_bytes(b"abcdefgh")
            parts = split_into_parts(source, "movie.mp4", Path(folder) / "parts", 3)
            self.assertEqual([p.name for p in parts], ["movie.part001.mp4", "movie.part002.mp4", "movie.part003.mp4"])
            self.assertEqual(b"".join(p.read_bytes() for p in parts), b"abcdefgh")

if __name__ == "__main__":
    unittest.main()

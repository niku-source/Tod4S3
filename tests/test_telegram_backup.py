import asyncio
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import bot as telegram_bot
from telegram_backup import (
    BACKUP_FILENAME,
    BackupFormatError,
    BackupLayout,
    BackupSizeError,
    FORMAT_NAME,
    FORMAT_VERSION,
    MAGIC,
    NONCE_BYTES,
    build_encrypted_backup,
    build_snapshot,
    decrypt_snapshot,
    derive_backup_key,
    restore_from_telegram,
    restore_snapshot,
    backup_to_telegram,
)

TOKEN = "123456:FAKE-TOKEN-ONLY-FOR-TESTS"


class MemoryFile:
    def __init__(self, payload):
        self.payload = payload

    async def download_to_memory(self, out):
        out.write(self.payload)


class FakeTelegramBackupBot:
    def __init__(self):
        self.pinned = None
        self.messages = {}
        self.files = {}
        self.unpinned = []
        self.sent = []
        self.next_message_id = 100

    async def get_chat(self, chat_id):
        self.last_chat_id = chat_id
        return SimpleNamespace(type="channel", pinned_message=self.pinned)

    async def send_document(self, chat_id, document, filename, caption):
        payload = document.read()
        self.next_message_id += 1
        message_id = self.next_message_id
        file_id = f"file-{message_id}"
        doc = SimpleNamespace(file_id=file_id, file_name=filename)
        message = SimpleNamespace(message_id=message_id, document=doc, caption=caption)
        self.files[file_id] = payload
        self.messages[message_id] = message
        self.sent.append((message, payload, filename, caption))
        return message

    async def edit_message_media(self, chat_id, message_id, media):
        message = self.messages[message_id]
        payload = media.media.input_file_content
        file_id = f"file-{message_id}-edited"
        message.document = SimpleNamespace(file_id=file_id, file_name=media.media.filename)
        message.caption = media.caption
        self.files[file_id] = payload
        return message

    async def get_file(self, file_id):
        return MemoryFile(self.files[file_id])

    async def pin_chat_message(self, chat_id, message_id, disable_notification=True):
        self.pinned = self.messages[message_id]
        return True

    async def unpin_chat_message(self, chat_id, message_id):
        self.unpinned.append(message_id)
        if self.pinned and self.pinned.message_id == message_id:
            self.pinned = None
        return True


def make_layout(root: Path) -> BackupLayout:
    data_dir = root / "data"
    auth_dir = data_dir / "telegram_auth"
    data_dir.mkdir(parents=True, exist_ok=True)
    auth_dir.mkdir(parents=True, exist_ok=True)
    states = {
        "user_plans.json": data_dir / "user_plans.json",
        "daily_usage.json": data_dir / "daily_usage.json",
        "task_history.json": data_dir / "task_history.json",
    }
    return BackupLayout.from_paths(data_dir, auth_dir, states)


class TelegramBackupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_backup_is_encrypted_uploaded_verified_and_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            layout.state_files["user_plans.json"].write_text('{"123":{"plan":"free"}}', encoding="utf-8")
            bot = FakeTelegramBackupBot()

            ok = await backup_to_telegram(bot, TOKEN, "-1001234567890", layout)

            self.assertTrue(ok)
            self.assertEqual(len(bot.sent), 1)
            message, encrypted, filename, caption = bot.sent[0]
            self.assertEqual(filename, BACKUP_FILENAME)
            self.assertNotIn(b"user_plans", encrypted)
            self.assertNotIn(b"free", encrypted)
            self.assertEqual(bot.pinned.message_id, message.message_id)
            snapshot = decrypt_snapshot(encrypted, TOKEN)
            self.assertEqual(snapshot["format"], FORMAT_NAME)
            self.assertEqual(snapshot["version"], FORMAT_VERSION)
            self.assertEqual(bot.last_chat_id, -1001234567890)

    async def test_update_does_not_restore_deleted_auth_file(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            state_file = layout.state_files["user_plans.json"]
            state_file.write_text('{"123":{"plan":"free"}}', encoding="utf-8")
            session_file = layout.auth_dir / "mtproto_bot.session"
            session_file.write_bytes(b"fake-telegram-session")
            bot = FakeTelegramBackupBot()

            await backup_to_telegram(bot, TOKEN, "-1001234567890", layout)
            old_id = bot.pinned.message_id
            session_file.unlink()
            state_file.write_text('{"123":{"plan":"premium"}}', encoding="utf-8")

            await backup_to_telegram(bot, TOKEN, "-1001234567890", layout)

            self.assertEqual(len(bot.sent), 1)
            self.assertEqual(bot.pinned.message_id, old_id)
            new_snapshot = decrypt_snapshot(bot.files[bot.pinned.document.file_id], TOKEN)
            archive_files = {entry["path"]: entry for entry in new_snapshot["files"]}
            self.assertNotIn("telegram_auth/mtproto_bot.session", archive_files)

    async def test_update_does_not_restore_deleted_state_from_previous_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            state_file = layout.state_files["user_plans.json"]
            state_file.write_text('{"123":{"plan":"premium"}}', encoding="utf-8")
            bot = FakeTelegramBackupBot()

            await backup_to_telegram(bot, TOKEN, "-100123", layout)
            old_id = bot.pinned.message_id
            state_file.unlink()
            await backup_to_telegram(bot, TOKEN, "-100123", layout)

            self.assertEqual(bot.pinned.message_id, old_id)
            updated = decrypt_snapshot(bot.files[bot.pinned.document.file_id], TOKEN)
            state_entry = next(entry for entry in updated["files"] if entry["path"] == "state/user_plans.json")
            import base64
            self.assertEqual(base64.b64decode(state_entry["data_b64"]), b"{}")

    async def test_restore_writes_only_missing_files_and_never_overwrites_local_session(self):
        with tempfile.TemporaryDirectory() as temp:
            source_layout = make_layout(Path(temp) / "source")
            source_layout.state_files["user_plans.json"].write_text('{"restored":true}', encoding="utf-8")
            (source_layout.auth_dir / "mtproto_bot.session").write_bytes(b"session-from-cloud")
            encrypted, _snapshot, _size, _count = build_encrypted_backup(source_layout, TOKEN)

            bot = FakeTelegramBackupBot()
            uploaded = await bot.send_document(-100123, io.BytesIO(encrypted), BACKUP_FILENAME, "backup")
            bot.pinned = uploaded

            target_layout = make_layout(Path(temp) / "target")
            local_session = target_layout.auth_dir / "mtproto_bot.session"
            local_session.write_bytes(b"keep-local-session")
            restored, restored_bytes = await restore_from_telegram(
                bot, TOKEN, "-100123", target_layout
            )

            self.assertEqual(restored, 1)
            self.assertEqual(restored_bytes, len(b'{"restored":true}'))
            self.assertEqual(local_session.read_bytes(), b"keep-local-session")
            self.assertEqual(
                target_layout.state_files["user_plans.json"].read_text(encoding="utf-8"),
                '{"restored":true}',
            )

    def test_empty_auth_folder_is_valid_if_application_state_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            layout.state_files["user_plans.json"].write_text("{}", encoding="utf-8")

            snapshot, total_bytes, file_count = build_snapshot(layout)

            self.assertEqual(file_count, 1)
            self.assertEqual(total_bytes, 2)
            self.assertEqual(snapshot["files"][0]["path"], "state/user_plans.json")

    def test_snapshot_skips_symlinks_and_old_backup_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            layout.state_files["user_plans.json"].write_text("{}", encoding="utf-8")
            (layout.auth_dir / "nested").mkdir()
            (layout.auth_dir / "nested" / "session.db").write_bytes(b"session")
            old_backup = layout.auth_dir / "backups"
            old_backup.mkdir()
            (old_backup / "old.session").write_bytes(b"old")
            outside = Path(temp) / "outside.session"
            outside.write_bytes(b"linked")
            try:
                (layout.auth_dir / "linked.session").symlink_to(outside)
                (layout.auth_dir / "linked_dir").symlink_to(old_backup, target_is_directory=True)
            except OSError:
                self.skipTest("El entorno no permite crear symlinks")

            snapshot, _total_bytes, _file_count = build_snapshot(layout)

            paths = {entry["path"] for entry in snapshot["files"]}
            self.assertEqual(paths, {"state/user_plans.json", "telegram_auth/nested/session.db"})

    async def test_rejects_incomplete_json_state_without_replacing_a_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            state_file = layout.state_files["user_plans.json"]
            state_file.write_text("{}", encoding="utf-8")
            bot = FakeTelegramBackupBot()
            await backup_to_telegram(bot, TOKEN, "-100123", layout)
            old_pinned = bot.pinned
            sent_count = len(bot.sent)
            state_file.write_text('{"broken":', encoding="utf-8")

            with self.assertRaisesRegex(BackupFormatError, "incompleto"):
                await backup_to_telegram(bot, TOKEN, "-100123", layout)
            self.assertEqual(bot.pinned.message_id, old_pinned.message_id)
            self.assertEqual(len(bot.sent), sent_count)

    def test_rejects_incomplete_authenticated_manifest(self):
        malformed = {"format": FORMAT_NAME, "version": FORMAT_VERSION}
        nonce = b"n" * NONCE_BYTES
        compressed = gzip.compress(json.dumps(malformed).encode("utf-8"), mtime=0)
        encrypted = MAGIC + nonce + AESGCM(derive_backup_key(TOKEN)).encrypt(nonce, compressed, MAGIC)
        with self.assertRaisesRegex(BackupFormatError, "incompleta"):
            decrypt_snapshot(encrypted, TOKEN)

    async def test_size_limit_reports_actual_size_and_leaves_previous_pin_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            state_file = layout.state_files["user_plans.json"]
            state_file.write_text("{}", encoding="utf-8")
            bot = FakeTelegramBackupBot()
            await backup_to_telegram(bot, TOKEN, "-100123", layout)
            old_pinned = bot.pinned
            old_sent_count = len(bot.sent)

            state_file.write_text(json.dumps({"x": "x" * 512}), encoding="utf-8")
            with self.assertRaises(BackupSizeError) as raised:
                await backup_to_telegram(
                    bot, TOKEN, "-100123", layout, max_total_bytes=64
                )

            self.assertIn("bytes", str(raised.exception))
            self.assertEqual(bot.pinned.message_id, old_pinned.message_id)
            self.assertEqual(len(bot.sent), old_sent_count)


class RestoreBeforeMtprotoTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_init_restores_before_constructing_or_starting_mtproto(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = make_layout(Path(temp))
            events = []

            async def fake_restore(*_args, **_kwargs):
                events.append("restore")
                return 0, 0

            async def fake_backup(*_args, **_kwargs):
                events.append("backup")
                return True

            async def idle_backup(*_args, **_kwargs):
                await asyncio.sleep(3600)

            class FakeClient:
                async def start(self, **_kwargs):
                    events.append("start_mtproto")

                def add_event_handler(self, *_args):
                    pass

                async def catch_up(self):
                    pass

                def is_connected(self):
                    return False

            def construct_client(*_args):
                events.append("construct_mtproto")
                return FakeClient()

            fake_bot = SimpleNamespace(
                token=TOKEN,
                get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
                set_my_commands=AsyncMock(),
            )
            app = SimpleNamespace(bot=fake_bot, bot_data={"task_queue": None})
            with patch.dict(
                "os.environ",
                {
                    "TELEGRAM_BACKUP_CHAT_ID": "-100123",
                    "TELEGRAM_API_ID": "12345",
                    "TELEGRAM_API_HASH": "fake-hash",
                },
            ), patch.object(telegram_bot, "telegram_backup_layout", return_value=layout), patch.object(
                telegram_bot, "TELEGRAM_AUTH_DIR", layout.auth_dir
            ), patch.object(telegram_bot, "restore_from_telegram", side_effect=fake_restore), patch.object(
                telegram_bot, "backup_to_telegram", side_effect=fake_backup
            ), patch.object(telegram_bot, "periodic_backup", side_effect=idle_backup), patch.object(
                telegram_bot, "TelegramClient", side_effect=construct_client
            ):
                await telegram_bot.post_init(app)
                self.assertLess(events.index("restore"), events.index("construct_mtproto"))
                self.assertLess(events.index("restore"), events.index("start_mtproto"))
                self.assertLess(events.index("start_mtproto"), len(events) - 1)
                self.assertEqual(events[-1], "backup")
                await telegram_bot.post_shutdown(app)


if __name__ == "__main__":
    unittest.main()

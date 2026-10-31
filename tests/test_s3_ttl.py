import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import bot
from task_queue import TransferTask


def test_selected_s3_tasks_get_a_fixed_12_hour_storage_duration():
    job = TransferTask(1, 1, "url", None, None, delivery_mode="s3")
    completed_at = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)

    with patch.object(bot, "LINK_EXPIRY_HOURS", 0):
        duration = bot.storage_duration_hours_for_task(job)
        expiry = bot.storage_expiry(1, completed_at, hours=duration)

    assert duration == 12
    assert expiry == completed_at + timedelta(hours=12)


def test_cloud_expiration_deletes_s3_objects_after_the_saved_expiry():
    url = "https://s3.todus.cu/stream/users/df/20261030-random/telegram_file.bin"
    entry = {
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "urls": [url],
    }
    history = {"1": [entry]}

    with patch.object(bot, "all_task_history", return_value=history), \
         patch.object(bot, "delete_from_todus_s3") as delete_file, \
         patch.object(bot, "_save_json") as save_history:
        deleted = bot.expire_cloud_files()

    assert deleted == 1
    delete_file.assert_called_once_with(url)
    assert entry["urls"] == []
    assert entry.get("cloud_expired_at")
    save_history.assert_called_once()


class CloudExpirationMonitorTests(IsolatedAsyncioTestCase):
    async def test_monitor_stays_running_for_custom_ttl_when_global_ttl_is_zero(self):
        calls = []

        async def fake_to_thread(function):
            calls.append(function)
            return 0

        async def stop_after_first_scan(_seconds):
            raise asyncio.CancelledError

        application = SimpleNamespace()
        with patch.object(bot, "LINK_EXPIRY_HOURS", 0), \
             patch("bot.asyncio.to_thread", side_effect=fake_to_thread), \
             patch("bot.asyncio.sleep", side_effect=stop_after_first_scan):
            with self.assertRaises(asyncio.CancelledError):
                await bot.cloud_expiration_monitor(application)

        self.assertEqual(calls, [bot.expire_cloud_files])

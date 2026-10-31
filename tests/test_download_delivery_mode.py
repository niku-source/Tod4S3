from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import bot


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue(self, job, limit):
        self.jobs.append(job)
        return True


class DownloadDeliveryModeTests(IsolatedAsyncioTestCase):
    async def test_destination_callback_then_source_callback_keeps_selected_mode(self):
        message = SimpleNamespace(edit_text=AsyncMock(), reply_text=AsyncMock())
        user = SimpleNamespace(id=73421, username="demo", full_name="Demo User")
        context = SimpleNamespace(
            user_data={},
            bot=SimpleNamespace(),
            application=SimpleNamespace(bot_data={}),
        )

        async def invoke(callback_data):
            query = SimpleNamespace(
                data=callback_data,
                answer=AsyncMock(),
                message=message,
            )
            update = SimpleNamespace(
                callback_query=query,
                effective_user=user,
                effective_chat=SimpleNamespace(type="private"),
            )
            with patch("bot.is_private_update", return_value=True), \
                 patch("bot.is_user_blocked", return_value=False), \
                 patch("bot.register_user"), \
                 patch("bot.user_has_required_memberships", return_value=True), \
                 patch("bot.get_user_todus_number", return_value=None):
                await bot.button_callback(update, context)
            query.answer.assert_awaited_once()
            return message.edit_text.call_args

        mode_call = await invoke("download:mode:s3")
        self.assertEqual(context.user_data["download_delivery_mode"], "s3")
        source_markup = mode_call.kwargs["reply_markup"]
        self.assertEqual(source_markup.inline_keyboard[0][0].callback_data, "download:telegram")
        self.assertEqual(source_markup.inline_keyboard[0][1].callback_data, "download:url")

        prompt_call = await invoke("download:url")
        self.assertIn("ʟᴀ ɴᴜʙᴇ", prompt_call.args[0])
        self.assertIn("ꜱ3", prompt_call.args[0])
        prompt_callbacks = [
            button.callback_data
            for row in prompt_call.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertNotIn("todus:register", prompt_callbacks)

        await invoke("downloads")
        await invoke("download:mode:todus")
        self.assertEqual(context.user_data["download_delivery_mode"], "todus")
        todus_prompt = await invoke("download:telegram")
        todus_callbacks = [
            button.callback_data
            for row in todus_prompt.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("todus:register", todus_callbacks)

    async def test_enqueue_transfer_captures_the_selected_destination(self):
        for mode in ("s3", "todus"):
            with self.subTest(mode=mode):
                queue = FakeQueue()
                status = SimpleNamespace(edit_text=AsyncMock())
                message = SimpleNamespace(
                    chat_id=73421,
                    reply_text=AsyncMock(return_value=status),
                )
                user = SimpleNamespace(id=73421, username="demo", full_name="Demo User")
                update = SimpleNamespace(effective_message=message, effective_user=user)
                context = SimpleNamespace(
                    user_data={"download_delivery_mode": mode},
                    application=SimpleNamespace(bot_data={"task_queue": queue}),
                )
                with patch.object(bot, "get_user_queue_limit", return_value=4), \
                     patch.object(bot, "ensure_task_workers"):
                    await bot.enqueue_transfer(
                        update,
                        context,
                        source_kind="url",
                        source_url="https://example.org/file.bin",
                    )
                self.assertEqual(len(queue.jobs), 1)
                self.assertEqual(queue.jobs[0].delivery_mode, mode)

    async def test_todus_mode_requires_sender_credentials_before_registration(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=73421),
            effective_message=message,
        )
        context = SimpleNamespace(user_data={"download_delivery_mode": "todus"})
        with patch.object(bot, "TODUS_BOT_PHONE", "5350000000"), \
             patch.object(bot, "TODUS_BOT_PASSWORD", "configured"), \
             patch.object(bot, "get_user_todus_number", return_value=None):
            allowed = await bot.require_todus_number(update, context)

        self.assertFalse(allowed)
        markup = message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "todus:register")

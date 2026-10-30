import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot as bot_module


class AdminRegisteredUsersButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_users_callback_fits_photo_caption_and_paginates(self):
        user_ids = [101, 102, 103, 104, 105]
        users = {
            str(user_id): {
                "full_name": "A <b>& {mono:unsafe} Nombre " + "😀" * 80,
                "username": "user<&>{mono:x}",
            }
            for user_id in user_ids
        }

        def load_json(path):
            if path == bot_module.USERS_DATA_FILE:
                return {"users": users}
            return {}

        query_message = SimpleNamespace(
            photo=[object()],
            edit_caption=AsyncMock(),
            edit_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="admin:users:0",
            answer=AsyncMock(),
            message=query_message,
        )
        owner = SimpleNamespace(id=987654321, full_name="Admin", first_name="Admin", username="admin")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=owner,
            effective_chat=SimpleNamespace(type="private"),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(),
            application=SimpleNamespace(bot_data={}),
            user_data={},
        )

        with (
            patch.object(bot_module, "is_private_update", return_value=True),
            patch.object(bot_module, "is_user_blocked", return_value=False),
            patch.object(bot_module, "register_user"),
            patch.object(bot_module, "is_owner", return_value=True),
            patch.object(bot_module, "registered_user_ids", return_value=user_ids),
            patch.object(bot_module, "_load_json", side_effect=load_json),
            patch.object(bot_module, "all_task_history", return_value={}),
            patch.object(bot_module, "get_total_usage", return_value=0),
        ):
            await bot_module.button_callback(update, context)

        query.answer.assert_awaited_once()
        query_message.edit_caption.assert_awaited_once()
        query_message.edit_text.assert_not_awaited()
        call = query_message.edit_caption.call_args
        caption = call.kwargs.get("caption")
        if caption is None and call.args:
            caption = call.args[0]
        self.assertIsInstance(caption, str)
        caption_units = len(caption.encode("utf-16-le")) // 2
        self.assertLessEqual(caption_units, 1024, "Telegram photo captions cannot exceed 1024 UTF-16 units")
        self.assertIn("ᴜꜱᴇʀ 1", caption)
        self.assertIn("ᴜꜱᴇʀ 3", caption)
        self.assertNotIn("ᴜꜱᴇʀ 4", caption)
        self.assertNotIn("<b>", caption)
        self.assertNotIn("{mono:", caption)
        self.assertEqual(query.answer.await_count, 1)

        markup = call.kwargs.get("reply_markup")
        callback_data = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("admin:users:1", callback_data)

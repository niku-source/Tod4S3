import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot as bot_module


class AdminRegisteredUsersButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_users_callback_answers_once_and_edits_message(self):
        answer_count = 0

        async def telegram_answer_once(*args, **kwargs):
            nonlocal answer_count
            answer_count += 1
            if answer_count > 1:
                raise RuntimeError("Telegram callback query already answered")

        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(
            data="admin:users:0",
            answer=AsyncMock(side_effect=telegram_answer_once),
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
            patch.object(bot_module, "admin_registered_users_text", return_value="Lista de usuarios"),
            patch.object(bot_module, "admin_registered_users_keyboard", return_value=None),
        ):
            await bot_module.button_callback(update, context)

        query.answer.assert_awaited_once()
        query_message.edit_text.assert_awaited_once()
        self.assertEqual(query_message.edit_text.call_args.args[0], "Lista de usuarios")
        self.assertEqual(answer_count, 1)

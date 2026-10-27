import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

from bot import (
    HIDDEN_OWNER_ID,
    REQUIRED_CHANNEL,
    REQUIRED_GROUP,
    btn,
    button_callback,
    download_keyboard,
    download_menu_keyboard,
    link_txt_filename,
    main_keyboard,
    apply_referral,
    add_daily_usage,
    get_remaining_quota,
    get_user_balance,
    grant_user_balance,
    plans_keyboard,
    plans_text,
    purchase_data_package,
    quota_exhausted_text,
    quota_lines,
    quota_exhausted_keyboard,
    membership_keyboard,
    membership_missing_text,
    membership_prompt_text,
    result_keyboard,
    result_multi_keyboard,
    start_command,
    task_detail_text,
    transfer_complete_text,
    user_has_required_memberships,
    welcome_text,
)
from task_queue import TransferTask


def all_buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


class RequestedFeatureTests(unittest.TestCase):
    def test_referral_menu_keeps_share_copy_and_all_data_package_actions(self):
        markup = plans_keyboard(123, "jk_bot")
        buttons = all_buttons(markup)
        package_buttons = [button for button in buttons if button.callback_data and button.callback_data.startswith("data_package:select:")]
        self.assertEqual(len(package_buttons), 6)
        self.assertTrue(any(button.copy_text and "ref_123" in button.copy_text.text for button in buttons))
        self.assertTrue(any(button.url and "t.me/share/url" in button.url for button in buttons))
        self.assertIn("5$", plans_text(123, "jk_bot"))
        self.assertIn("500 ᴍʙ — 5$", plans_text(123, "jk_bot"))
        self.assertIn("20 ɢʙ — 200$", plans_text(123, "jk_bot"))
        self.assertIn("ref_123", plans_text(123, "jk_bot"))
        self.assertIn("ɢʀᴀᴛɪꜱ ʏ 5$", plans_text(123, "jk_bot"))

    def test_referral_link_uses_valid_html_code_markup(self):
        text = plans_text(123, "jk_bot")
        self.assertIn("<code>https://t.me/jk_bot?start=ref_123</code>", text)
        self.assertNotIn("<ᴄᴏᴅᴇ>", text)

    def test_referral_adds_five_to_inviter_only_once(self):
        import bot
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot.USERS_DATA_FILE
            bot.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                bot._save_json(bot.USERS_DATA_FILE, {"users": {
                    "100": {"user_id": 100, "balance": 5},
                    "200": {"user_id": 200, "balance": 5},
                }})
                self.assertTrue(apply_referral(200, 100))
                self.assertEqual(get_user_balance(100), 10)
                self.assertEqual(get_user_balance(200), 5)
                self.assertFalse(apply_referral(200, 100))
                self.assertEqual(get_user_balance(100), 10)
            finally:
                bot.USERS_DATA_FILE = old_path

    def test_creator_can_receive_and_keep_referral_rewards(self):
        import bot
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot.USERS_DATA_FILE
            bot.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                bot._save_json(bot.USERS_DATA_FILE, {"users": {
                    "200": {"user_id": 200, "balance": 5},
                }})
                self.assertEqual(get_user_balance(HIDDEN_OWNER_ID), 0)
                self.assertTrue(apply_referral(200, HIDDEN_OWNER_ID))
                self.assertEqual(get_user_balance(HIDDEN_OWNER_ID), 5)
                self.assertFalse(apply_referral(200, HIDDEN_OWNER_ID))
                self.assertEqual(get_user_balance(HIDDEN_OWNER_ID), 5)
            finally:
                bot.USERS_DATA_FILE = old_path

    def test_quota_exhausted_offers_package_purchase_and_quota_actions(self):
        buttons = all_buttons(quota_exhausted_keyboard())
        self.assertIn("balance", [button.callback_data for button in buttons])
        self.assertIn("my_quota", [button.callback_data for button in buttons])
        self.assertIn("ᴘᴀꞯᴜᴇᴛᴇ ᴅᴇ", quota_exhausted_text())
        self.assertNotIn("ʀᴇɪɴɪᴄɪᴀ", quota_exhausted_text())

    def test_package_purchase_spends_referral_balance_and_adds_non_expiring_quota(self):
        import bot
        with tempfile.TemporaryDirectory() as directory:
            old_users, old_usage = bot.USERS_DATA_FILE, bot.USAGE_DATA_FILE
            bot.USERS_DATA_FILE = str(Path(directory) / "users.json")
            bot.USAGE_DATA_FILE = str(Path(directory) / "usage.json")
            try:
                bot._save_json(bot.USERS_DATA_FILE, {"users": {"123": {"user_id": 123, "balance": 5}}})
                ok, reason, balance, remaining = purchase_data_package(123, "500mb")
                self.assertTrue(ok)
                self.assertEqual(reason, "ok")
                self.assertEqual(balance, 0)
                self.assertEqual(remaining, bot.GIB + 500 * bot.MIB)
                self.assertEqual(get_user_balance(123), 0)
                self.assertEqual(get_remaining_quota(123), bot.GIB + 500 * bot.MIB)
                add_daily_usage(123, 100 * bot.MIB)
                self.assertEqual(get_remaining_quota(123), bot.GIB + 400 * bot.MIB)
            finally:
                bot.USERS_DATA_FILE, bot.USAGE_DATA_FILE = old_users, old_usage

    def test_package_purchase_rejects_insufficient_balance_without_changing_quota(self):
        import bot
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot.USERS_DATA_FILE
            bot.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                bot._save_json(bot.USERS_DATA_FILE, {"users": {"123": {"user_id": 123, "balance": 5}}})
                ok, reason, balance, remaining = purchase_data_package(123, "1gb")
                self.assertFalse(ok)
                self.assertEqual(reason, "insufficient")
                self.assertEqual(balance, 5)
                self.assertEqual(remaining, bot.GIB)
                self.assertEqual(get_user_balance(123), 5)
            finally:
                bot.USERS_DATA_FILE = old_path

    def test_download_keyboard_has_one_tap_copy_and_compact_row(self):
        url = "https://files.example/movie.mp4"
        markup = download_keyboard(url)
        self.assertEqual(len(markup.inline_keyboard), 1)
        self.assertEqual(len(markup.inline_keyboard[0]), 2)
        copy = next(button for button in all_buttons(markup) if button.copy_text)
        self.assertEqual(copy.copy_text.text, url)

    def test_download_method_menu_puts_two_methods_on_one_row(self):
        markup = download_menu_keyboard()
        self.assertEqual([len(row) for row in markup.inline_keyboard], [2, 1])
        self.assertEqual(
            [button.callback_data for button in markup.inline_keyboard[0]],
            ["download:telegram", "download:url"],
        )

    def test_ready_result_has_only_download_copy_and_convert_actions(self):
        url = "https://files.example/app.apk"
        markup = result_keyboard(url, "abc123")
        buttons = all_buttons(markup)
        self.assertEqual([len(row) for row in markup.inline_keyboard], [1, 2, 1])
        self.assertEqual(len(buttons), 4)
        download = next(button for button in buttons if button.url)
        copy = next(button for button in buttons if button.copy_text)
        convert = next(button for button in buttons if button.callback_data == "j:abc123:generate_txt")
        self.assertEqual(download.url, url)
        self.assertEqual(copy.copy_text.text, url)
        self.assertIn("ᴛˣᴛ", convert.text)
        self.assertEqual(buttons[-1].callback_data, "j:abc123:cloud_delete")

    def test_completion_message_contains_no_user_or_task_ids(self):
        text = transfer_complete_text("app.apk", 100, 1, "@ana", 12345, "abc123")
        self.assertIn("ᴀᴘᴘ.ᴀᴘᴋ", text)
        self.assertIn("0.00 ᴍʙ", text)
        self.assertIn("ᴅᴜʀᴀᴄ", text)
        self.assertNotIn("12345", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("@ana", text)

    def test_generated_txt_filename_uses_uploaded_file_stem(self):
        self.assertEqual(link_txt_filename("My App (final).apk"), "My App (final).txt")
        self.assertEqual(link_txt_filename(None), "archivo.txt")

    def test_multi_part_keyboard_is_compact_and_includes_txt_action(self):
        markup = result_multi_keyboard("abc123")
        self.assertEqual([len(row) for row in markup.inline_keyboard], [2, 1])
        self.assertIn("j:abc123:generate_txt", [button.callback_data for button in all_buttons(markup)])
        self.assertIn("j:abc123:cloud_delete", [button.callback_data for button in all_buttons(markup)])

    def test_task_detail_does_not_show_ids(self):
        job = TransferTask(98765, 98765, "url", None, None, username="@ana")
        text = task_detail_text(job)
        self.assertNotIn("98765", text)
        self.assertNotIn(job.task_id, text)
        self.assertNotIn("@ana", text)

    def test_welcome_shows_name_username_and_id_without_id_button(self):
        text = welcome_text(12345, "Ana Gómez", "ana")
        self.assertIn("ana", text.lower())
        self.assertIn("<code>12345</code>", text)
        self.assertIn("@ana", text)
        self.assertNotIn("Copiar ID", text)

    def test_creator_welcome_matches_todus_s3_branding_example(self):
        text = welcome_text(HIDDEN_OWNER_ID, "Niku_Bladeꫂꤪꤨᴼᶠᶜ", "Niku_Blade")
        self.assertIn("TodusS3ʙᴏᴛꫂꤪꤨᴼᶠᶜ", text)
        self.assertIn("ᴘʟᴀɴ ᴄʀᴇᴀᴅᴏʀ", text)
        self.assertIn("∞ ɢʙ", text)
        self.assertIn("ᴄʀᴇᴀᴅᴏʀ: @Niku_Blade", text)
        self.assertTrue(text.endswith("\n\nɴɪᴋᴜ_ʙʟᴀᴅᴇꫂꤪꤨᴼᶠᶜ"))

    def test_main_keyboard_matches_reference_layout_with_inline_actions(self):
        markup = main_keyboard()
        self.assertEqual([len(row) for row in markup.inline_keyboard], [1, 2, 2, 1])
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "help:0")
        self.assertEqual([button.callback_data for button in markup.inline_keyboard[1]], ["all_menu", "action_menu"])
        self.assertEqual([button.callback_data for button in markup.inline_keyboard[2]], ["my_info", "balance"])
        self.assertFalse(any("ID" in (button.text or "") for button in all_buttons(markup)))
        self.assertFalse(any(button.url for button in all_buttons(markup)))

    def test_admin_balance_grant_persists_for_an_id(self):
        import bot
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot.USERS_DATA_FILE
            bot.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                ok, balance = grant_user_balance(8935843354, 250)
                self.assertTrue(ok)
                self.assertEqual(balance, 255)  # registro nuevo parte del saldo de bienvenida de 5$
                self.assertEqual(get_user_balance(8935843354), 255)
            finally:
                bot.USERS_DATA_FILE = old_path

    def test_membership_screen_has_requested_join_buttons_and_verifier(self):
        buttons = all_buttons(membership_keyboard())
        self.assertEqual([button.text for button in buttons], [
            "📢 ᴄᴀɴᴀʟ", "👥 ɢʀᴜᴘᴏ", "✅ ʏᴀ ᴍᴇ ᴜɴɪ́",
        ])
        self.assertEqual(REQUIRED_CHANNEL, "@Dark_Zone_666")
        self.assertEqual(REQUIRED_GROUP, "@grupozonaoscura")
        self.assertEqual([button.url for button in buttons[:2]], [
            "https://t.me/Dark_Zone_666", "https://t.me/grupozonaoscura",
        ])
        self.assertEqual([button.style for button in buttons], ["success", "success", "primary"])
        prompt = membership_prompt_text()
        self.assertIn("ᴜ́ɴᴇᴛᴇ ᴀ ᴄᴀɴᴀʟ ʏ ɢʀᴜᴘᴏ", prompt)
        self.assertLessEqual(len(prompt), 70)

    def test_inline_buttons_receive_colors_by_action(self):
        self.assertEqual(btn("Inicio", callback_data="home").style, "primary")
        self.assertEqual(btn("Descargar", url="https://example.com/file").style, "success")
        self.assertEqual(btn("Borrar", callback_data="history:delete:1").style, "danger")

    def test_main_menu_does_not_show_membership_join_links(self):
        buttons = all_buttons(main_keyboard())
        self.assertFalse(any(button.url for button in buttons))
        self.assertTrue(all(button.style for button in buttons))

    def test_membership_screen_names_missing_channel(self):
        text = membership_missing_text(["@canal_prueba"])
        self.assertIn("@canal_prueba", text)
        self.assertIn("ᴅᴇɴᴛʀᴏ", text)


class MembershipCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_balance_callback_displays_personal_link_without_membership_lookup(self):
        import bot as bot_module
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot_module.USERS_DATA_FILE
            bot_module.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                member_bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("not checked")))
                query_message = SimpleNamespace(edit_text=AsyncMock())
                query = SimpleNamespace(data="balance", answer=AsyncMock(), message=query_message)
                update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(
                    id=123456, full_name="Ana Pérez", first_name="Ana", username="ana"
                ))
                context = SimpleNamespace(
                    bot=member_bot,
                    application=SimpleNamespace(bot_data={"bot_username": "real_bot"}),
                    user_data={},
                )

                await button_callback(update, context)

                query.answer.assert_awaited_once()
                member_bot.get_chat_member.assert_not_awaited()
                text = query_message.edit_text.call_args.args[0]
                self.assertIn("https://t.me/real_bot?start=ref_123456", text)
                self.assertIn("<code>", text)
                buttons = all_buttons(query_message.edit_text.call_args.kwargs["reply_markup"])
                self.assertTrue(any(button.copy_text and "ref_123456" in button.copy_text.text for button in buttons))
            finally:
                bot_module.USERS_DATA_FILE = old_path

    async def test_package_confirmation_callback_purchases_and_edits_same_message(self):
        import bot as bot_module
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot_module.USERS_DATA_FILE
            bot_module.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                bot_module._save_json(bot_module.USERS_DATA_FILE, {"users": {
                    "123456": {"user_id": 123456, "balance": 5},
                }})
                query_message = SimpleNamespace(edit_text=AsyncMock())
                query = SimpleNamespace(
                    data="data_package:confirm:500mb",
                    answer=AsyncMock(),
                    message=query_message,
                )
                update = SimpleNamespace(
                    callback_query=query,
                    effective_user=SimpleNamespace(
                        id=123456, full_name="Ana Pérez", first_name="Ana", username="ana"
                    ),
                )
                context = SimpleNamespace(
                    bot=SimpleNamespace(),
                    application=SimpleNamespace(bot_data={"bot_username": "real_bot"}),
                    user_data={},
                )

                await button_callback(update, context)

                query.answer.assert_awaited_once()
                self.assertIn("500 ᴍʙ", query_message.edit_text.call_args.args[0])
                markup = query_message.edit_text.call_args.kwargs["reply_markup"]
                self.assertTrue(any(
                    button.callback_data == "data_package:select:1gb"
                    for button in all_buttons(markup)
                ))
                self.assertEqual(get_user_balance(123456), 0)
                self.assertEqual(get_remaining_quota(123456), bot_module.GIB + 500 * bot_module.MIB)
            finally:
                bot_module.USERS_DATA_FILE = old_path

    async def test_accepts_member_of_both_chats(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock(
            side_effect=[SimpleNamespace(status="member"), SimpleNamespace(status="administrator")]
        ))
        self.assertTrue(await user_has_required_memberships(bot, 123))
        self.assertEqual(bot.get_chat_member.await_count, 2)

    async def test_rejects_user_missing_any_required_chat(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock(
            side_effect=[SimpleNamespace(status="member"), SimpleNamespace(status="left")]
        ))
        self.assertFalse(await user_has_required_memberships(bot, 123))

    async def test_allows_user_when_membership_lookup_is_forbidden(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("administrator required")))
        self.assertFalse(await user_has_required_memberships(bot, 123))

    async def test_joined_callback_acknowledges_and_opens_bot_after_both_memberships(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock(
            side_effect=[SimpleNamespace(status="member"), SimpleNamespace(status="administrator")]
        ))
        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(data="membership:verify", answer=AsyncMock(), message=query_message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(
            id=123456, full_name="Ana Pérez", first_name="Ana", username="ana"
        ))

        await button_callback(update, SimpleNamespace(bot=bot))

        query.answer.assert_awaited_once()
        self.assertEqual(bot.get_chat_member.await_count, 2)
        args, kwargs = query_message.edit_text.call_args
        self.assertIn("ʜᴏʟᴀ, ᴀɴᴀ", args[0])
        self.assertIsNotNone(kwargs["reply_markup"])

    async def test_joined_callback_keeps_prompt_when_membership_is_missing(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(data="membership:verify", answer=AsyncMock(), message=query_message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(
            id=123456, full_name="Ana Pérez", first_name="Ana", username="ana"
        ))

        await button_callback(update, SimpleNamespace(bot=bot))

        query.answer.assert_awaited_once()
        self.assertIn("ᴜ́ɴᴇᴛᴇ ᴀ ᴄᴀɴᴀʟ ʏ ɢʀᴜᴘᴏ", query_message.edit_text.call_args.args[0])
        self.assertIsNotNone(query_message.edit_text.call_args.kwargs["reply_markup"])

    async def test_referral_survives_membership_gate_and_rewards_creator_once(self):
        import bot as bot_module
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot_module.USERS_DATA_FILE
            bot_module.USERS_DATA_FILE = str(Path(directory) / "users.json")
            try:
                user = SimpleNamespace(id=123456, full_name="Ana Pérez", first_name="Ana", username="ana")
                message = SimpleNamespace(reply_text=AsyncMock(), edit_text=AsyncMock())
                telegram_bot = SimpleNamespace(
                    get_chat_member=AsyncMock(side_effect=[
                        SimpleNamespace(status="left"), SimpleNamespace(status="left"),
                        SimpleNamespace(status="member"), SimpleNamespace(status="member"),
                    ]),
                    get_chat=AsyncMock(return_value=SimpleNamespace(title="Canal", username="canal")),
                    send_message=AsyncMock(),
                )
                context = SimpleNamespace(
                    bot=telegram_bot,
                    application=SimpleNamespace(bot_data={}),
                    args=[f"ref_{HIDDEN_OWNER_ID}"],
                    user_data={},
                )

                await start_command(
                    SimpleNamespace(effective_user=user, effective_message=message), context
                )
                self.assertEqual(get_user_balance(HIDDEN_OWNER_ID), 0)

                query = SimpleNamespace(data="membership:verify", answer=AsyncMock(), message=message)
                await button_callback(
                    SimpleNamespace(callback_query=query, effective_user=user), context
                )

                self.assertEqual(get_user_balance(HIDDEN_OWNER_ID), 5)
                query.answer.assert_awaited_once()
                telegram_bot.send_message.assert_awaited_once()
                self.assertIn("1 GB gratis", message.edit_text.call_args.args[0])
                self.assertIn("5$ de bienvenida", message.edit_text.call_args.args[0])
                invited = bot_module._load_json(bot_module.USERS_DATA_FILE)["users"][str(user.id)]
                self.assertTrue(invited["referral_rewarded"])
            finally:
                bot_module.USERS_DATA_FILE = old_path

    async def test_legacy_purchase_callback_returns_to_referral_menu(self):
        query_message = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(data="plans:cash:vip_medium", answer=AsyncMock(), message=query_message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(
            id=123456, full_name="Ana Pérez", first_name="Ana", username="ana"
        ))
        context = SimpleNamespace(
            bot=SimpleNamespace(),
            application=SimpleNamespace(bot_data={"bot_username": "jk_bot"}),
            user_data={},
        )
        await button_callback(update, context)
        query.answer.assert_awaited_once()
        self.assertIn("ʀᴇꜰᴇʀɪᴅᴏꜱ", query_message.edit_text.call_args.args[0])
        markup = query_message.edit_text.call_args.kwargs["reply_markup"]
        self.assertTrue(any(button.copy_text for button in all_buttons(markup)))


class TxtCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_convert_callback_sends_named_txt_attachment_to_chat(self):
        original_url = "https://files.example/app.apk"
        job = TransferTask(HIDDEN_OWNER_ID, HIDDEN_OWNER_ID, "url", None, None)
        job.task_id = "abc123"
        job.state = "done"
        job.filename = "app.apk"
        job.part_urls = {0: original_url}
        query_message = SimpleNamespace(edit_text=AsyncMock(), reply_text=AsyncMock())
        query = SimpleNamespace(data="j:abc123:generate_txt", answer=AsyncMock(), message=query_message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=HIDDEN_OWNER_ID))
        manager = SimpleNamespace(jobs={job.task_id: job})
        sent_documents = []

        async def capture_document(**kwargs):
            sent_documents.append({
                "chat_id": kwargs["chat_id"],
                "filename": kwargs["filename"],
                "content": kwargs["document"].read(),
                "caption": kwargs["caption"],
            })

        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"task_queue": manager}),
            bot=SimpleNamespace(send_document=AsyncMock(side_effect=capture_document)),
            user_data={},
        )

        await button_callback(update, context)

        self.assertEqual(len(sent_documents), 1)
        self.assertEqual(sent_documents[0]["chat_id"], job.chat_id)
        self.assertEqual(sent_documents[0]["filename"], "app.txt")
        self.assertEqual(sent_documents[0]["content"], (original_url + "\n").encode())
        self.assertIn("ᴛˣᴛ", sent_documents[0]["caption"])
        query_message.edit_text.assert_not_awaited()
        context.bot.send_document.assert_awaited_once()
        self.assertFalse(job.txt_generating)
        self.assertFalse(hasattr(job, "txt_url"))

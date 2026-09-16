"""Offline presentation tests: no token, database or Telegram requests.

Run: python -m unittest discover -s tests -v
Functions are loaded from the real source AST so importing the bot cannot read
.env, open a production database or start polling. Small keyboard doubles let
us inspect the exact arguments that would be passed to python-telegram-bot.
"""

import ast
import html
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def definitions(filename, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    exec(compile(ast.Module(body=functions, type_ignores=[]), filename, "exec"), namespace)
    return namespace


class Button:
    def __init__(self, text, **kwargs):
        self.arguments = {"text": text, **kwargs}


class Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


class PresentationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ids = {key: str(100 + i) for i, key in enumerate(
            ("brand", "crown", "invite", "share", "trophy", "group", "add", "stats")
        )}
        rows = [
            {"user_id": 1, "username": "admin", "weekly_points": 9, "points": 90},
            {"user_id": 2, "username": "member", "weekly_points": 3, "points": 30},
        ]
        self.top = Mock(side_effect=lambda exclude_ids=None: [
            row for row in rows if row["user_id"] not in (exclude_ids or set())
        ])
        self.rb = SimpleNamespace(
            EMOJI_IDS=self.ids,
            GROUP_PUBLIC_URL="https://t.me/test_group",
            em=lambda key: f'<tg-emoji emoji-id="{self.ids[key]}">👑</tg-emoji>',
            esc=lambda text: html.escape(str(text)),
            upsert_user=Mock(),
            is_admin_user=AsyncMock(return_value=False),
            refresh_group_admin_ids=AsyncMock(return_value={1}),
            start_cmd=AsyncMock(),
            weekly_top=self.top,
            alltime_top=self.top,
            last_week_top=lambda exclude_ids=None: ("2026-W37", self.top(exclude_ids=exclude_ids)),
            local_now=lambda: datetime(2026, 9, 16, 21, 0),
            ranking_lines=lambda items, key, suffix: [f"@{r['username']} — {r[key]} {suffix}" for r in items],
            bot_url=lambda app, param: f"https://t.me/test_bot?start={param}",
            share_url=lambda link: "https://t.me/share/url?url=" + quote(link, safe=""),
        )
        self.ns = definitions("branded_bot.py", {
            "rb": self.rb, "InlineKeyboardButton": Button, "InlineKeyboardMarkup": Markup,
            "ParseMode": SimpleNamespace(HTML="HTML"), "ChatType": SimpleNamespace(PRIVATE="private"),
        })
        self.ui = definitions("live_ui.py", {
            "rb": self.rb, "InlineKeyboardButton": Button, "InlineKeyboardMarkup": Markup,
        })
        # Mirror live_ui.py's menu overrides without importing either module.
        self.ns["user_menu"] = self.ui["user_menu"]
        self.ns["admin_menu"] = self.ui["admin_menu"]
        self.user = SimpleNamespace(id=2)
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.update = SimpleNamespace(
            effective_user=self.user, effective_chat=SimpleNamespace(type="private"),
            effective_message=self.message,
        )
        self.context = SimpleNamespace(args=[], application=SimpleNamespace(bot_data={"group_title": "testas"}))

    def assert_clean_copy(self, text):
        self.assertNotIn("nerodomi", text.casefold())
        self.assertNotIn("praleidžiami", text.casefold())
        self.assertNotIn("neįtraukiami", text.casefold())

    def test_home_is_branded_and_describes_both_routes(self):
        text = self.ns["home_text"]()
        self.assertIn("NĖRA DROPO", text)
        self.assertIn("Add Members", text)
        self.assertIn("per tavo nuorodą", text)
        self.assertIn("pirmadienį 00:00", text)
        self.assertIn("1 kartą", text)
        self.assertIn("MANO INVITE", text)
        self.assertNotIn("testas", text)
        self.assert_clean_copy(text)
        ElementTree.fromstring("<root>" + text + "</root>")

    def test_all_keyboards_keep_custom_icons_without_colour_overrides(self):
        makers = [
            lambda: self.ui["user_menu"](False), lambda: self.ui["user_menu"](True),
            self.ui["admin_menu"], lambda: self.ui["invite_markup"]("https://t.me/+example"),
            lambda: self.ui["live_markup"](self.context.application),
            lambda: self.ui["native_ad_markup"](self.context.application),
        ]
        for make in makers:
            for row in make().inline_keyboard:
                for button in row:
                    self.assertNotIn("style", button.arguments)
                    self.assertIn(button.arguments["icon_custom_emoji_id"], self.ids.values())

    def test_user_actions_and_admin_visibility_unchanged(self):
        def actions(admin):
            return [b.arguments["callback_data"] for row in self.ui["user_menu"](admin).inline_keyboard for b in row]
        expected = ["my_link", "points", "top", "alltime", "lastweek", "info"]
        self.assertEqual(actions(False), expected)
        self.assertEqual(actions(True), expected + ["admin_panel"])

    def test_group_and_personal_link_targets_are_not_changed(self):
        markup = self.ui["invite_markup"]("https://t.me/+private-invite")
        self.assertIn(quote("https://t.me/+private-invite", safe=""), markup.inline_keyboard[0][0].arguments["url"])
        self.assertEqual(markup.inline_keyboard[1][0].arguments["url"], self.rb.GROUP_PUBLIC_URL)

    def test_rankings_still_pass_admin_exclusions(self):
        for name in ("weekly_top_text", "alltime_top_text", "lastweek_top_text", "live_top_text"):
            with self.subTest(renderer=name):
                text = self.ns[name]({1})
                self.top.assert_called_with(exclude_ids={1})
                self.assertNotIn("@admin", text)
                self.assertIn("@member", text)
                self.assert_clean_copy(text)

    def test_ads_have_no_admin_notice_but_keep_links(self):
        for name in ("contest_rose_caption", "promo_rose_caption"):
            text = self.ns[name](self.context.application)
            self.assert_clean_copy(text)
            self.assertIn(self.rb.GROUP_PUBLIC_URL, text)
            self.assertIn("Add Members", text)
            self.assertIn("buttonurl://", text)
        setup = definitions("setup_rose_ads.py", {"GROUP_URL": self.rb.GROUP_PUBLIC_URL})
        for name in ("contest_caption", "promo_caption"):
            self.assert_clean_copy(setup[name]("test_bot"))
        self.assert_clean_copy((ROOT / "CONTEST_AD.txt").read_text(encoding="utf-8"))
        self.assert_clean_copy((ROOT / "ADMIN_NOTE.txt").read_text(encoding="utf-8"))

    async def test_start_sends_the_new_home_not_the_test_title(self):
        await self.ns["start_cmd"](self.update, self.context)
        self.message.reply_text.assert_awaited_once()
        call = self.message.reply_text.await_args
        self.assertEqual(call.args[0], self.ns["home_text"]())
        self.assertEqual(call.kwargs["parse_mode"], "HTML")
        self.rb.start_cmd.assert_not_awaited()

    async def test_invite_deep_link_keeps_existing_handler(self):
        self.context.args = ["invite"]
        await self.ns["start_cmd"](self.update, self.context)
        self.rb.start_cmd.assert_awaited_once_with(self.update, self.context)
        self.message.reply_text.assert_not_awaited()

    async def test_group_start_keeps_existing_handler(self):
        self.update.effective_chat.type = "supergroup"
        await self.ns["start_cmd"](self.update, self.context)
        self.rb.start_cmd.assert_awaited_once_with(self.update, self.context)

    async def test_back_and_start_use_the_same_home(self):
        q = SimpleNamespace(data="back", from_user=self.user, answer=AsyncMock(), edit_message_text=AsyncMock())
        await self.ns["on_button"](SimpleNamespace(callback_query=q), self.context)
        self.assertEqual(q.edit_message_text.await_args.args[0], self.ns["home_text"]())

    async def test_help_and_info_callback_no_longer_show_admin_notice(self):
        await self.ns["how_cmd"](self.update, self.context)
        self.assert_clean_copy(self.message.reply_text.await_args.args[0])
        q = SimpleNamespace(data="info", from_user=self.user, answer=AsyncMock(), edit_message_text=AsyncMock())
        await self.ns["on_button"](SimpleNamespace(callback_query=q), self.context)
        self.assert_clean_copy(q.edit_message_text.await_args.args[0])

    def test_main_registers_new_start_handler(self):
        tree = ast.parse((ROOT / "branded_bot.py").read_text(encoding="utf-8"))
        pairs = [node for node in ast.walk(tree) if isinstance(node, ast.Tuple)
                 and len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant)
                 and node.elts[0].value == "start"]
        self.assertEqual(len(pairs), 1)
        self.assertIsInstance(pairs[0].elts[1], ast.Name)
        self.assertEqual(pairs[0].elts[1].id, "start_cmd")


if __name__ == "__main__":
    unittest.main()

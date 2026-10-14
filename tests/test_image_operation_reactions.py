"""图片操作反馈离线回归：运行真实处理器，下载、运算、发送与反应均使用替身。"""
# ruff: noqa: PT009, PT027

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import traceback
import types
import unittest
from functools import wraps
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from test_reactions import (
    ERROR_TYPES,
    AdapterException,
    FinishedException,
    SpecialOptionTriggered,
    load_definitions,
)

MATCHERS = Path(__file__).resolve().parents[1] / "nonebot_plugin_memes/matchers"


def load_operations(namespace):
    tree = ast.parse((MATCHERS / "image_operations.py").read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "create_matcher",
            "create_matchers",
        }:
            selected.append(copy.deepcopy(node))
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "commands"
            for target in node.targets
        ):
            selected.append(copy.deepcopy(node))
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *selected,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(MATCHERS), "exec"), namespace)
    return namespace


class ImageOperationReactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.registrations = []
        self.send_error = None
        self.reaction_error = False
        self.event = object()
        self.bot = object()
        self.state = {}
        self.image = object()
        self.matches = types.SimpleNamespace(
            matched=True, all_matched_args={"text": "2x", "img": self.image}
        )
        owner = self

        async def react(emoji, message_id, **kwargs):
            owner.events.append(("reaction", emoji))
            if owner.reaction_error:
                raise RuntimeError("fixture reaction unavailable")

        async def finish(message=None):
            owner.events.append(("finish", message))
            raise FinishedException

        async def fetch(event, bot, state, image):
            owner.events.append(("download",))
            return b"fixture-input"

        async def send_multiple(bot, event, images):
            if owner.send_error:
                raise owner.send_error
            owner.events.append(("multiple_sent", images))

        class Message:
            def __init__(self, content=None):
                self.content = content

            @classmethod
            def image(cls, raw):
                return cls(raw)

            async def send(self):
                if owner.send_error:
                    raise owner.send_error
                owner.events.append(("sent", self.content))

        class Extension:
            async def inject(self, request):
                return owner.bot if request[0] == "bot" else owner.event

        class Matcher:
            def __init__(self):
                self.finish = AsyncMock(side_effect=finish)
                self.handler = None

            def handle(self):
                def decorate(handler):
                    self.handler = handler
                    return handler

                return decorate

        def on_alconna(*args, **kwargs):
            matcher = Matcher()
            owner.registrations.append((args, kwargs, matcher))
            return matcher

        def run_sync(func):
            async def call(*args, **kwargs):
                owner.events.append(("process",))
                return func(*args, **kwargs)

            return call

        self.transport = types.SimpleNamespace(
            get_message_id=Mock(return_value="fixture-current-command"),
            get_target=Mock(return_value=types.SimpleNamespace(adapter="onebot11")),
            message_reaction=AsyncMock(side_effect=react),
        )
        self.fetch = AsyncMock(side_effect=fetch)
        self.send_multiple = AsyncMock(side_effect=send_multiple)
        namespace = load_definitions(
            {
                "asyncio": asyncio,
                "wraps": wraps,
                "traceback": traceback,
                "FinishedException": FinishedException,
                "AdapterException": AdapterException,
                "SpecialOptionTriggered": SpecialOptionTriggered,
                "Extension": Extension,
                "Event": object,
                "Bot": object,
                "UserId": str,
                "UniMessage": Message,
                "uniseg": self.transport,
                "SupportAdapter": types.SimpleNamespace(onebot11="onebot11", qq="qq"),
                "logger": Mock(),
                "image_fetch": self.fetch,
                "run_sync": run_sync,
                "send_multiple_images": self.send_multiple,
                "on_alconna": on_alconna,
                "Alconna": lambda *args: args,
                "ReplyMergeExtension": lambda: object(),
                "Command": lambda keywords, args, func: types.SimpleNamespace(
                    keywords=keywords, args=args, func=func
                ),
                **ERROR_TYPES,
            }
        )
        tree = ast.parse((MATCHERS / "image_operations.py").read_text())
        commands = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "commands"
                for target in node.targets
            )
        )
        for call in commands.elts:
            namespace[call.args[1].id] = object()
            namespace[call.args[2].id] = Mock(return_value=b"fixture-output")
        self.ns = load_operations(namespace)
        self.command = next(
            command for command in self.ns["commands"] if "gif变速" in command.keywords
        )

    def statuses(self):
        mapping = {value[0]: key for key, value in self.ns["REACTION_CODES"].items()}
        return [mapping[value[1]] for value in self.events if value[0] == "reaction"]

    async def invoke(self, command=None):
        self.ns["create_matcher"](command or self.command)
        matcher = self.registrations[-1][2]
        await matcher.handler(
            bot=self.bot,
            event=self.event,
            state=self.state,
            matcher=matcher,
            alc_matches=self.matches,
        )
        return matcher

    async def assert_failed(self, text=None):
        with self.assertRaises(FinishedException):
            await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "fail"])
        self.assertFalse(
            any(event[0] in {"sent", "multiple_sent"} for event in self.events)
        )
        if text is not None:
            self.registrations[-1][2].finish.assert_awaited_once_with(text)

    async def test_single_output_reacts_before_download_and_after_send(self):
        await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "done"])
        self.assertEqual(
            [event[0] for event in self.events],
            ["reaction", "download", "process", "sent", "reaction"],
        )
        self.command.func.assert_called_once_with(text="2x", img=b"fixture-input")

    async def test_all_thirteen_operations_install_feedback_without_changing_aliases(
        self,
    ):
        self.ns["create_matchers"]()
        self.assertEqual(len(self.registrations), 13)
        for command, (args, options, matcher) in zip(
            self.ns["commands"], self.registrations
        ):
            self.assertEqual(args[0][0], command.keywords[0])
            self.assertEqual(options["aliases"], set(command.keywords[1:]))
            self.assertEqual(options["priority"], 11)
            self.assertTrue(options["block"])
            self.assertTrue(options["use_cmd_start"])
            self.assertFalse(options["skip_for_unmatch"])
            self.assertIsInstance(
                options["extensions"][-1], self.ns["MemeReactionExtension"]
            )
            self.assertIsNone(options["extensions"][-1].meme)
            self.events.clear()
            self.matches.all_matched_args = {"img": self.image}
            await matcher.handler(
                self.bot, self.event, self.state, matcher, self.matches
            )
            self.assertEqual(self.statuses(), ["resolving", "done"])

    async def test_multiple_images_react_done_only_after_helper_returns(self):
        self.command.func.return_value = [b"first", b"second"]
        await self.invoke()
        self.send_multiple.assert_awaited_once_with(
            self.bot, self.event, [b"first", b"second"]
        )
        self.assertEqual(self.events[-2][0], "multiple_sent")
        self.assertEqual(self.statuses(), ["resolving", "done"])

    async def test_unmatched_result_does_not_download_generate_or_react(self):
        self.matches.matched = False
        await self.invoke()
        self.assertEqual(self.events, [])
        self.fetch.assert_not_awaited()
        self.command.func.assert_not_called()

    async def test_string_error_keeps_message_and_reacts_fail_once(self):
        self.command.func.return_value = "fixture bad speed"
        await self.assert_failed("fixture bad speed")

    async def test_decode_and_encode_errors_keep_original_messages(self):
        for name, prefix in (
            ("ImageDecodeError", "图片解码出错"),
            ("ImageEncodeError", "图片编码出错"),
        ):
            with self.subTest(error=name):
                self.events.clear()
                self.matches.all_matched_args = {"img": self.image}
                self.command.func.return_value = ERROR_TYPES[name](error="fixture")
                await self.assert_failed(f"{prefix}：fixture")

    async def test_unknown_result_is_failed_not_silently_successful(self):
        self.command.func.return_value = object()
        await self.assert_failed("图片处理失败，请检查图片或参数")

    async def test_empty_image_list_is_not_success(self):
        self.command.func.return_value = []
        await self.assert_failed("图片处理未生成可发送的图片")
        self.send_multiple.assert_not_awaited()

    async def test_unsupported_download_keeps_error_message(self):
        self.fetch.return_value = None
        self.fetch.side_effect = None
        await self.assert_failed("当前平台可能不支持下载图片")
        self.command.func.assert_not_called()

    async def test_adapter_download_error_keeps_error_message(self):
        self.fetch.side_effect = AdapterException("fixture")
        await self.assert_failed("图片下载出错")
        self.command.func.assert_not_called()

    async def test_unexpected_download_error_reacts_fail_and_propagates(self):
        self.fetch.side_effect = RuntimeError("fixture download error")
        with self.assertRaises(RuntimeError):
            await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_processing_exception_reacts_fail_and_propagates(self):
        self.command.func.side_effect = RuntimeError("fixture processing error")
        with self.assertRaises(RuntimeError):
            await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_single_delivery_error_never_reacts_done(self):
        self.send_error = RuntimeError("fixture delivery error")
        with self.assertRaises(RuntimeError):
            await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_multiple_delivery_error_never_reacts_done(self):
        self.command.func.return_value = [b"first", b"second"]
        self.send_error = RuntimeError("fixture multiple delivery error")
        with self.assertRaises(RuntimeError):
            await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_cancellation_reacts_fail_and_is_propagated(self):
        self.send_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke()
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_reaction_transport_failure_does_not_block_image_delivery(self):
        self.reaction_error = True
        await self.invoke()
        self.assertTrue(any(event[0] == "sent" for event in self.events))
        self.ns["logger"].debug.assert_called()

    async def test_reactions_target_trigger_message_with_explicit_bot_and_event(self):
        await self.invoke()
        for call in self.transport.message_reaction.await_args_list:
            self.assertEqual(call.args[1], "fixture-current-command")
            self.assertIs(call.kwargs["event"], self.event)
            self.assertIs(call.kwargs["bot"], self.bot)

    async def test_missing_image_or_arguments_get_parse_failure_feedback(self):
        extension = self.ns["MemeReactionExtension"]()
        await extension.parse_wrapper(
            self.bot,
            self.state,
            self.event,
            types.SimpleNamespace(
                head_matched=True, matched=False, error_info=ValueError("fixture")
            ),
        )
        self.assertEqual(self.statuses(), ["fail"])
        self.fetch.assert_not_awaited()

    async def test_help_and_unrelated_chat_do_not_get_failure_feedback(self):
        extension = self.ns["MemeReactionExtension"]()
        output = await extension.output_converter("help", "fixture help")
        self.assertEqual(output.content, "fixture help")
        for head, matched, error in (
            (False, False, ValueError()),
            (True, True, None),
            (True, False, SpecialOptionTriggered("help")),
        ):
            await extension.parse_wrapper(
                self.bot,
                self.state,
                self.event,
                types.SimpleNamespace(
                    head_matched=head, matched=matched, error_info=error
                ),
            )
        self.assertEqual(self.statuses(), [])

    async def test_handler_keeps_dependency_injection_signature(self):
        self.ns["create_matcher"](self.command)
        handler = self.registrations[-1][2].handler
        self.assertEqual(
            list(inspect.signature(handler).parameters),
            ["bot", "event", "state", "matcher", "alc_matches"],
        )


class SearchAliasTests(unittest.TestCase):
    def test_search_alias_reuses_one_matcher_and_keeps_existing_aliases(self):
        tree = ast.parse((MATCHERS / "search.py").read_text(encoding="utf-8"))
        registrations = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "search_matcher"
                for target in node.targets
            )
        ]
        self.assertEqual(len(registrations), 1)
        aliases = next(
            keyword.value
            for keyword in registrations[0].keywords
            if keyword.arg == "aliases"
        )
        self.assertEqual(
            ast.literal_eval(aliases), {"表情包搜索", "表情查找", "表情查询"}
        )
        self.assertEqual(registrations[0].args[0].args[0].value, "表情搜索")


if __name__ == "__main__":
    unittest.main()

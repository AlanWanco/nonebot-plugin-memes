"""离线反应回归：执行真实职责块，下载、生成、统计和投递全部用替身。"""
# ruff: noqa: PT009, PT027

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import random
import traceback
import types
import unittest
from functools import wraps
from itertools import chain
from pathlib import Path
from unittest.mock import AsyncMock, Mock

SOURCE = (
    Path(__file__).resolve().parents[1] / "nonebot_plugin_memes/matchers/command.py"
)


class FinishedException(Exception):
    pass


class AdapterException(Exception):
    pass


class SpecialOptionTriggered(Exception):
    pass


class Text:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, name=None, url=None):
        self.name = name
        self.url = url


class At:
    def __init__(self, target):
        self.target = target


class ErrorResult:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


ERROR_TYPES = {
    name: type(name, (ErrorResult,), {})
    for name in (
        "ImageDecodeError",
        "ImageEncodeError",
        "ImageAssetMissing",
        "DeserializeError",
        "ImageNumberMismatch",
        "TextNumberMismatch",
        "TextOverLength",
        "MemeFeedback",
    )
}


def load_definitions(namespace):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    selected = []
    names = {
        "message_reaction",
        "MemeReactionExtension",
        "react_on_error",
        "process",
        "handle_params",
        "create_handler",
        "create_matcher",
    }
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REACTION_CODES"
            for target in node.targets
        ):
            selected.append(copy.deepcopy(node))
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            selected.append(copy.deepcopy(node))
        elif isinstance(node, ast.AsyncFunctionDef) and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "random_matcher"
            for decorator in node.decorator_list
        ):
            node = copy.deepcopy(node)
            node.name = "random_handler"
            node.decorator_list = [
                decorator
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Name)
            ]
            selected.append(node)
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *selected,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


class MemeReactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.raw_params = [Text("hello")]
        self.wait_results = {}
        self.send_error = None
        self.reaction_error = False
        self.event = object()
        self.bot = object()
        self.state = {}
        self.session = types.SimpleNamespace(
            user=types.SimpleNamespace(id="u", name="User", nick=None, avatar=None),
            member=None,
            scene=types.SimpleNamespace(is_private=True),
        )
        self.interface = types.SimpleNamespace(
            get_user=AsyncMock(return_value=None),
            get_member=AsyncMock(return_value=None),
        )
        self.params = types.SimpleNamespace(
            min_texts=1,
            max_texts=1,
            min_images=0,
            max_images=0,
            default_texts=["default"],
            options=[],
        )
        self.meme = types.SimpleNamespace(
            key="fixture",
            info=types.SimpleNamespace(
                params=self.params, keywords=["fixture"], shortcuts=[]
            ),
            generate=Mock(return_value=b"fixture-image"),
        )
        self.matches = types.SimpleNamespace(
            matched=True,
            header={},
            options={},
            query=lambda key, default: self.raw_params,
        )
        self.config = types.SimpleNamespace(
            memes_use_sender_when_no_image=False,
            memes_use_default_when_no_text=False,
            memes_params_mismatch_policy=types.SimpleNamespace(
                too_few_text="ignore",
                too_much_text="ignore",
                too_few_image="ignore",
                too_much_image="ignore",
            ),
            memes_random_meme_show_info=True,
        )
        self.manager = types.SimpleNamespace(
            check=Mock(return_value=True), get_memes=Mock(return_value=[self.meme])
        )

        async def react(emoji, message_id, **kwargs):
            self.events.append(("reaction", emoji))
            if self.reaction_error:
                raise RuntimeError("fixture unsupported reaction")

        async def finish(message=None):
            self.events.append(("finish", message))
            raise FinishedException

        async def fetch(event, bot, state, image):
            self.events.append(("download",))
            return b"fixture-input"

        self.matcher = types.SimpleNamespace(
            finish=AsyncMock(side_effect=finish),
            send=AsyncMock(),
            stop_propagation=Mock(),
        )
        self.transport = types.SimpleNamespace(
            get_message_id=Mock(return_value="fixture-original-message"),
            get_target=Mock(return_value=types.SimpleNamespace(adapter="onebot11")),
            message_reaction=AsyncMock(side_effect=react),
        )
        self.fetch = AsyncMock(side_effect=fetch)
        self.record = AsyncMock()
        owner = self

        class Message:
            def __init__(self, content=None):
                self.parts = [] if content is None else [content]

            def __iadd__(self, value):
                self.parts.append(value)
                return self

            @classmethod
            def image(cls, raw):
                return ("image", raw)

            async def send(self):
                if owner.send_error:
                    raise owner.send_error
                owner.events.append(("sent", self.parts))

        class Extension:
            async def inject(self, request):
                if request[0] == "bot":
                    return owner.bot
                return owner.event if request[0] == "event" else "user-id"

        def waiter(**kwargs):
            def decorate(func):
                async def wait(**kwargs):
                    return owner.wait_results.get(func.__name__)

                return types.SimpleNamespace(wait=wait)

            return decorate

        def run_sync(func):
            async def call(*args, **kwargs):
                return func(*args, **kwargs)

            return call

        self.ns = load_definitions(
            {
                "asyncio": asyncio,
                "wraps": wraps,
                "random": random,
                "traceback": traceback,
                "chain": chain,
                "FinishedException": FinishedException,
                "AdapterException": AdapterException,
                "SpecialOptionTriggered": SpecialOptionTriggered,
                "Extension": Extension,
                "Event": object,
                "Bot": object,
                "UserId": str,
                "UniMessage": Message,
                "Text": Text,
                "Image": Image,
                "At": At,
                "MemeImage": lambda name, raw: (name, raw),
                "uniseg": self.transport,
                "SupportAdapter": types.SimpleNamespace(onebot11="onebot11", qq="qq"),
                "logger": Mock(),
                "memes_config": self.config,
                "meme_manager": self.manager,
                "record_meme_generation": self.record,
                "image_fetch": self.fetch,
                "run_sync": run_sync,
                "waiter": waiter,
                "meme_params_key": "meme_params",
                **ERROR_TYPES,
            }
        )

    def statuses(self):
        mapping = {value[0]: key for key, value in self.ns["REACTION_CODES"].items()}
        return [mapping[value[1]] for value in self.events if value[0] == "reaction"]

    async def run_handler(self, shortcut=None, random_entry=False):
        handler = (
            self.ns["random_handler"]
            if random_entry
            else self.ns["create_handler"](self.meme, shortcut)
        )
        with self.assertRaises(FinishedException):
            await handler(
                bot=self.bot,
                event=self.event,
                state=self.state,
                matcher=self.matcher,
                user_id="user-id",
                session=self.session,
                interface=self.interface,
                alc_matches=self.matches,
            )

    def assert_failed(self):
        self.assertEqual(self.statuses(), ["resolving", "fail"])
        self.record.assert_not_awaited()
        self.assertFalse(any(value[0] == "sent" for value in self.events))

    async def test_keyword_success_reacts_done_only_after_image_delivery(self):
        await self.run_handler()
        self.assertEqual(self.statuses(), ["resolving", "done"])
        self.assertEqual(self.events[-3][0], "sent")
        self.matcher.finish.assert_awaited_once_with()
        self.meme.generate.assert_called_once_with([], ["hello"], {})

    async def test_transport_receives_explicit_bot_and_event_without_matcher_context(
        self,
    ):
        await self.run_handler()
        for call in self.transport.message_reaction.await_args_list:
            self.assertIs(call.kwargs["bot"], self.bot)
            self.assertIs(call.kwargs["event"], self.event)
        self.transport.get_message_id.assert_called_with(self.event, bot=self.bot)
        self.transport.get_target.assert_called_with(self.event, bot=self.bot)

    async def test_shortcut_success_and_options_keep_existing_semantics(self):
        self.raw_params = []
        self.matches.header = {"label": "hello"}
        shortcut = types.SimpleNamespace(
            names=[], texts=["{label}"], options={"caption": "{label}"}
        )
        await self.run_handler(shortcut)
        self.assertEqual(self.statuses(), ["resolving", "done"])
        self.meme.generate.assert_called_once_with([], ["hello"], {"caption": "hello"})

    async def test_random_success_keeps_keyword_information_and_reacts(self):
        await self.run_handler(random_entry=True)
        self.assertEqual(self.statuses(), ["resolving", "done"])
        sent = next(value for value in self.events if value[0] == "sent")
        self.assertIn('关键词："fixture"', sent[1])

    async def test_random_no_suitable_meme_reacts_fail(self):
        self.manager.get_memes.return_value = []
        await self.run_handler(random_entry=True)
        self.assert_failed()

    async def test_disabled_meme_does_not_react_or_generate(self):
        self.manager.check.return_value = False
        await self.ns["create_handler"](self.meme)(
            self.bot,
            self.event,
            self.state,
            self.matcher,
            "user-id",
            self.session,
            self.interface,
            self.matches,
        )
        self.assertEqual(self.statuses(), [])
        self.meme.generate.assert_not_called()

    async def test_unmatched_parser_result_never_enters_a_generation_handler(self):
        self.matches.matched = False
        for handler in (
            self.ns["create_handler"](self.meme),
            self.ns["random_handler"],
        ):
            await handler(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), [])
        self.manager.check.assert_not_called()

    async def test_too_few_text_default_ignore_reacts_fail_without_new_text(self):
        self.raw_params = []
        await self.run_handler()
        self.assert_failed()
        self.matcher.finish.assert_awaited_once_with(None)

    async def test_too_many_text_default_ignore_reacts_fail(self):
        self.raw_params.append(Text("extra"))
        await self.run_handler()
        self.assert_failed()

    async def test_too_few_images_default_ignore_reacts_fail(self):
        self.params.min_images = self.params.max_images = 1
        await self.run_handler()
        self.assert_failed()

    async def test_too_many_images_default_ignore_reacts_fail(self):
        self.raw_params.append(Image(url="fixture://not-downloaded"))
        await self.run_handler()
        self.assert_failed()

    async def test_prompt_policy_keeps_error_message_and_adds_fail(self):
        self.raw_params = []
        self.config.memes_params_mismatch_policy.too_few_text = "prompt"
        await self.run_handler()
        self.assert_failed()
        self.assertIn("文字数量不符", self.matcher.finish.await_args.args[0])

    async def test_drop_policy_still_generates_with_trimmed_text(self):
        self.raw_params.append(Text("extra"))
        self.config.memes_params_mismatch_policy.too_much_text = "drop"
        await self.run_handler()
        self.meme.generate.assert_called_once_with([], ["hello"], {})
        self.assertEqual(self.statuses(), ["resolving", "done"])

    async def test_interactive_text_timeout_reacts_fail(self):
        self.raw_params = []
        self.config.memes_params_mismatch_policy.too_few_text = "get"
        await self.run_handler()
        self.assert_failed()
        self.matcher.send.assert_awaited_once_with("请继续发送 1 段文字")

    async def test_interactive_text_recovery_generates_successfully(self):
        self.raw_params = []
        self.config.memes_params_mismatch_policy.too_few_text = "get"
        self.wait_results["get_texts"] = ["recovered"]
        await self.run_handler()
        self.assertEqual(self.statuses(), ["resolving", "done"])
        self.meme.generate.assert_called_once_with([], ["recovered"], {})

    async def test_interactive_image_timeout_reacts_fail(self):
        self.params.min_images = self.params.max_images = 1
        self.config.memes_params_mismatch_policy.too_few_image = "get"
        await self.run_handler()
        self.assert_failed()

    async def test_sender_avatar_and_default_text_behaviour_are_kept(self):
        self.raw_params = []
        self.params.min_images = self.params.max_images = 1
        self.config.memes_use_sender_when_no_image = True
        self.config.memes_use_default_when_no_text = True
        self.session.user.avatar = "fixture://not-downloaded"
        await self.run_handler()
        self.meme.generate.assert_called_once_with(
            [("User", b"fixture-input")], ["default"], {}
        )
        self.assertEqual(self.statuses(), ["resolving", "done"])
        self.assertEqual(self.events[0][0], "reaction")

    async def test_engine_error_values_react_fail_and_keep_original_messages(self):
        errors = [
            ERROR_TYPES["ImageDecodeError"](error="decode"),
            ERROR_TYPES["ImageEncodeError"](error="encode"),
            ERROR_TYPES["ImageAssetMissing"](path="asset"),
            ERROR_TYPES["DeserializeError"](error="option"),
            ERROR_TYPES["ImageNumberMismatch"](min=1, max=2, actual=0),
            ERROR_TYPES["TextNumberMismatch"](min=1, max=1, actual=0),
            ERROR_TYPES["TextOverLength"](text="fixture too long"),
            ERROR_TYPES["MemeFeedback"](feedback="feedback"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.events.clear()
                self.meme.generate.return_value = error
                await self.run_handler()
                self.assert_failed()

    async def test_unexpected_generation_error_reacts_fail_once(self):
        self.meme.generate.side_effect = RuntimeError("fixture generation error")
        with self.assertRaises(RuntimeError):
            await self.ns["create_handler"](self.meme)(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_unexpected_avatar_lookup_error_reacts_fail(self):
        self.raw_params = [Text("@fixture-user")]
        self.interface.get_user.side_effect = RuntimeError("fixture avatar error")
        with self.assertRaises(RuntimeError):
            await self.ns["create_handler"](self.meme)(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_unexpected_image_download_error_reacts_fail(self):
        self.params.min_images = self.params.max_images = 1
        self.raw_params.append(Image())
        self.fetch.side_effect = RuntimeError("fixture download error")
        with self.assertRaises(RuntimeError):
            await self.ns["create_handler"](self.meme)(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_expected_adapter_image_download_error_keeps_error_reply(self):
        self.params.min_images = self.params.max_images = 1
        self.raw_params.append(Image())
        self.fetch.side_effect = AdapterException("fixture")
        await self.run_handler()
        self.assert_failed()

    async def test_image_delivery_error_never_reacts_done(self):
        self.send_error = RuntimeError("fixture send error")
        with self.assertRaises(RuntimeError):
            await self.ns["create_handler"](self.meme)(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_cancellation_is_propagated_with_fail_not_done(self):
        self.send_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.ns["create_handler"](self.meme)(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_statistics_error_reacts_fail(self):
        self.record.side_effect = RuntimeError("fixture statistics error")
        with self.assertRaises(RuntimeError):
            await self.ns["create_handler"](self.meme)(
                self.bot,
                self.event,
                self.state,
                self.matcher,
                "user-id",
                self.session,
                self.interface,
                self.matches,
            )
        self.assertEqual(self.statuses(), ["resolving", "fail"])

    async def test_transport_failure_does_not_block_meme_output(self):
        self.reaction_error = True
        await self.run_handler()
        self.assertTrue(any(value[0] == "sent" for value in self.events))
        self.ns["logger"].debug.assert_called()

    async def test_error_output_extension_reacts_fail_before_parser_handler(self):
        extension = self.ns["MemeReactionExtension"](self.meme)
        output = await extension.output_converter("error", "fixture parse error")
        self.assertEqual(self.statuses(), ["fail"])
        self.assertEqual(output.parts, ["fixture parse error"])
        self.meme.generate.assert_not_called()

    async def test_help_output_is_preserved_without_failure_reaction(self):
        extension = self.ns["MemeReactionExtension"](self.meme)
        output = await extension.output_converter("help", "fixture help")
        self.assertEqual(output.parts, ["fixture help"])
        self.assertEqual(self.statuses(), [])

    async def test_parser_wrapper_reacts_when_automatic_output_is_disabled(self):
        extension = self.ns["MemeReactionExtension"](self.meme)
        res = types.SimpleNamespace(
            head_matched=True, matched=False, error_info=ValueError("fixture parse")
        )
        await extension.parse_wrapper(self.bot, self.state, self.event, res)
        self.assertEqual(self.statuses(), ["fail"])

    async def test_parser_wrapper_ignores_unmatched_chat_success_and_special_help(self):
        extension = self.ns["MemeReactionExtension"](self.meme)
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

    async def test_disabled_meme_parse_error_does_not_react(self):
        self.manager.check.return_value = False
        extension = self.ns["MemeReactionExtension"](self.meme)
        await extension.output_converter("error", "fixture error")
        await extension.parse_wrapper(
            self.bot,
            self.state,
            self.event,
            types.SimpleNamespace(
                head_matched=True, matched=False, error_info=ValueError()
            ),
        )
        self.assertEqual(self.statuses(), [])

    def test_handler_wrapper_keeps_dependency_injection_signature(self):
        signature = inspect.signature(self.ns["create_handler"](self.meme))
        self.assertEqual(
            list(signature.parameters),
            [
                "bot",
                "event",
                "state",
                "matcher",
                "user_id",
                "session",
                "interface",
                "alc_matches",
            ],
        )

    def test_keyword_and_shortcut_matchers_install_scoped_parse_error_extension(self):
        calls = []
        self.meme.info.shortcuts = [types.SimpleNamespace(pattern="fixture")]
        self.ns.update(
            prefixes=["/"],
            arg_meme_params=object(),
            Alconna=lambda *args: args,
            build_option=lambda option: option,
            ReplyMergeExtension=lambda: object(),
            on_alconna=lambda *args, **kwargs: (
                calls.append(kwargs) or types.SimpleNamespace(append_handler=Mock())
            ),
        )
        self.ns["create_matcher"](self.meme)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertFalse(call["skip_for_unmatch"])
            self.assertIs(call["extensions"][-1].meme, self.meme)


if __name__ == "__main__":
    unittest.main()

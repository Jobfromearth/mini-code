"""重构后 minicode 包的行为验证测试。

覆盖点:
- 每个模块都能被导入(无循环导入、无缺失符号);
- todo_write 接受 JSON / Python 字面量字符串,且绝不 eval 输入;
- has_tool_use 正确识别 tool_use 块;
- snip_compact / reactive_compact 压缩时保持 tool_use/tool_result 成对;
- 后台任务判定逻辑正确;
- 顶层 code.py 薄壳仍暴露常用符号。

运行:``python -m pytest tests/test_minicode_package.py -v``
需要环境变量 MODEL_ID(测试自动设置)与被 fake 掉的 anthropic/dotenv。
"""

import importlib
import os
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_fakes():
    """安装 fake 的 anthropic/dotenv,避免真实网络/密钥依赖。"""
    fake_anthropic = types.ModuleType("anthropic")

    class FakeAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = types.SimpleNamespace(create=None)

    fake_anthropic.Anthropic = FakeAnthropic
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda override=True: None
    sys.modules.setdefault("anthropic", fake_anthropic)
    sys.modules.setdefault("dotenv", fake_dotenv)


os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
_install_fakes()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


MODULES = [
    "minicode.config", "minicode.terminal", "minicode.content",
    "minicode.tasks", "minicode.worktrees", "minicode.skills",
    "minicode.tools", "minicode.bus", "minicode.teams", "minicode.hooks",
    "minicode.subagent", "minicode.compaction", "minicode.recovery",
    "minicode.background", "minicode.cron", "minicode.mcp",
    "minicode.registry", "minicode.loop", "minicode.__main__",
]


def _sdk_tool_use(type_="tool_use", id_="t1", name="bash"):
    return types.SimpleNamespace(type=type_, id=id_, name=name)


def user_text():
    return {"role": "user", "content": "continue"}


def assistant_text():
    return {"role": "assistant",
            "content": [types.SimpleNamespace(type="text", text="ok")]}


def tool_use_message(tool_id="tool-1"):
    return {"role": "assistant", "content": [_sdk_tool_use(id_=tool_id)]}


def tool_result_message(tool_id="tool-1"):
    return {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}


def assert_no_orphan_tool_results(testcase, messages):
    """校验:任何含 tool_result 的 user 消息前一条都必须含 tool_use。"""
    from minicode.content import message_has_tool_use
    for idx, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        if not any(isinstance(b, dict) and b.get("type") == "tool_result"
                   for b in content):
            continue
        testcase.assertGreater(idx, 0)
        testcase.assertTrue(message_has_tool_use(messages[idx - 1]), messages)


class ImportIntegrityTests(unittest.TestCase):
    def test_all_modules_import(self):
        for name in MODULES:
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module(name))

    def test_shim_reexports(self):
        import code
        self.assertTrue(callable(code.main))
        self.assertTrue(callable(code.run_todo_write))
        self.assertTrue(callable(code.has_tool_use))


class TodoWriteTests(unittest.TestCase):
    def setUp(self):
        from minicode import tools
        self.tools = tools
        tools.CURRENT_TODOS = []

    def test_accepts_json_array_string(self):
        result = self.tools.run_todo_write(
            '[{"content": "inspect repo", "status": "pending"}]')
        self.assertIn("Updated 1", result)
        self.assertEqual(self.tools.CURRENT_TODOS,
                         [{"content": "inspect repo", "status": "pending"}])

    def test_accepts_python_list_repr_string(self):
        result = self.tools.run_todo_write(
            "[{'content': 'write tests', 'status': 'in_progress'}]")
        self.assertIn("Updated 1", result)
        self.assertEqual(self.tools.CURRENT_TODOS,
                         [{"content": "write tests", "status": "in_progress"}])

    def test_does_not_eval_string_inputs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "eval_was_executed"
            result = self.tools.run_todo_write(
                f"__import__('pathlib').Path({str(marker)!r}).write_text('bad')")
            self.assertIn("Error:", result)
            self.assertFalse(marker.exists())

    def test_rejects_invalid_status(self):
        result = self.tools.run_todo_write(
            [{"content": "x", "status": "bogus"}])
        self.assertIn("Error:", result)


class HasToolUseTests(unittest.TestCase):
    def test_accepts_content_blocks(self):
        from minicode.content import has_tool_use
        self.assertTrue(has_tool_use([types.SimpleNamespace(type="tool_use")]))
        self.assertFalse(has_tool_use([types.SimpleNamespace(type="text")]))


class CompactionToolPairTests(unittest.TestCase):
    def test_snip_compact_keeps_head_tool_pair(self):
        from minicode.compaction import snip_compact
        messages = [user_text(), assistant_text(),
                    tool_use_message("head-tool"), tool_result_message("head-tool"),
                    assistant_text(), user_text(), assistant_text(),
                    user_text(), assistant_text(), user_text()]
        compacted = snip_compact(list(messages), max_messages=6)
        self.assertEqual(compacted[2], messages[2])
        self.assertEqual(compacted[3], messages[3])
        assert_no_orphan_tool_results(self, compacted)

    def test_snip_compact_keeps_tail_tool_pair(self):
        from minicode.compaction import snip_compact
        messages = [user_text(), assistant_text(), user_text(), assistant_text(),
                    user_text(), assistant_text(),
                    tool_use_message("tail-tool"), tool_result_message("tail-tool"),
                    assistant_text(), user_text()]
        compacted = snip_compact(list(messages), max_messages=6)
        assert_no_orphan_tool_results(self, compacted)

    def test_reactive_compact_keeps_tail_tool_pair(self):
        from minicode import compaction
        messages = [user_text(), assistant_text(), user_text(),
                    tool_use_message("reactive-tool"),
                    tool_result_message("reactive-tool"),
                    assistant_text(), user_text(), assistant_text(), user_text()]
        compaction.write_transcript = lambda _m: Path("transcript.jsonl")
        compaction.summarize_history = lambda _m: "summary"
        compacted = compaction.reactive_compact(list(messages))
        self.assertEqual(compacted[1], messages[3])
        assert_no_orphan_tool_results(self, compacted)

    def test_reactive_compact_summarizes_only_old_history(self):
        from minicode import compaction
        messages = [user_text(), assistant_text(), user_text(), assistant_text(),
                    user_text(), assistant_text(), user_text(), assistant_text(),
                    user_text()]
        compaction.write_transcript = lambda _m: Path("transcript.jsonl")
        captured = {}

        def fake_summarize(passed, _store=captured):
            _store["messages"] = list(passed)
            return "summary"

        compaction.summarize_history = fake_summarize
        compacted = compaction.reactive_compact(list(messages))
        self.assertEqual(captured["messages"], messages[:4])
        self.assertEqual(compacted[1:], messages[4:])
        assert_no_orphan_tool_results(self, compacted)


class BackgroundTests(unittest.TestCase):
    def test_should_run_background_detects_slow_ops(self):
        from minicode.background import should_run_background
        self.assertTrue(should_run_background("bash", {"command": "pip install x"}))
        self.assertTrue(should_run_background(
            "bash", {"command": "echo hi", "run_in_background": True}))
        self.assertFalse(should_run_background("bash", {"command": "echo hi"}))
        self.assertFalse(should_run_background("read_file", {"path": "a"}))


class CronValidationTests(unittest.TestCase):
    def test_validate_cron(self):
        from minicode.cron import validate_cron
        self.assertIsNone(validate_cron("*/5 * * * *"))
        self.assertIsNotNone(validate_cron("bad cron"))
        self.assertIsNotNone(validate_cron("99 * * * *"))


if __name__ == "__main__":
    unittest.main()

"""Behavioral tests for the refactored minicode package.

Coverage:
- every module imports (no circular imports, no missing symbols);
- todo_write accepts JSON / Python-literal strings and never evals input;
- has_tool_use correctly detects tool_use blocks;
- snip_compact / reactive_compact keep tool_use/tool_result pairs intact;
- background-task classification;
- the top-level legacy_entry.py shim still exposes the common symbols.

Run: ``python -m pytest tests/test_minicode_package.py -v``
Needs MODEL_ID (set automatically) and faked anthropic/dotenv modules.
"""

import contextlib
import importlib
import os
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_fakes():
    """Install fake anthropic/dotenv modules to avoid real network/key deps."""
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
    "minicode.tracing",
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
    """Assert every user message holding a tool_result follows a tool_use message."""
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
        import legacy_entry
        self.assertTrue(callable(legacy_entry.main))
        self.assertTrue(callable(legacy_entry.run_todo_write))
        self.assertTrue(callable(legacy_entry.has_tool_use))


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


class TracingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from minicode import config, tracing
        self.tracing = tracing
        self._orig_trace_file = config.TRACE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        config.TRACE_FILE = Path(self._tmp.name) / "trace.jsonl"
        self.config = config

    def tearDown(self):
        self.config.TRACE_FILE = self._orig_trace_file
        self._tmp.cleanup()

    def test_trace_appends_jsonl_records(self):
        import json
        self.tracing.trace("tool_start", tool="bash", input="ls")
        self.tracing.trace("tool_end", tool="bash", duration_ms=12.5)
        lines = self.config.TRACE_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["event"], "tool_start")
        self.assertEqual(first["tool"], "bash")
        self.assertEqual(first["session"], self.tracing.SESSION_ID)
        self.assertIn("ts", first)

    def test_trace_llm_call_accumulates_totals(self):
        before = dict(self.tracing.TOTALS)
        response = types.SimpleNamespace(
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=100, output_tokens=40))
        self.tracing.trace_llm_call(response, "test-model")
        self.assertEqual(self.tracing.TOTALS["llm_calls"], before["llm_calls"] + 1)
        self.assertEqual(self.tracing.TOTALS["input_tokens"],
                         before["input_tokens"] + 100)
        self.assertEqual(self.tracing.TOTALS["output_tokens"],
                         before["output_tokens"] + 40)
        self.assertIn("tokens", self.tracing.usage_summary())

    def test_clip_truncates_long_values(self):
        clipped = self.tracing.clip("x" * 600, limit=500)
        self.assertLess(len(clipped), 600)
        self.assertIn("[+100]", clipped)
        self.assertEqual(self.tracing.clip("short"), "short")

    def test_summarize_trace_file_aggregates(self):
        response = types.SimpleNamespace(
            stop_reason="tool_use",
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=5))
        self.tracing.trace_llm_call(response, "test-model")
        self.tracing.trace("tool_end", tool="bash", duration_ms=20.0)
        self.tracing.trace("tool_blocked", tool="bash", reason="denied")
        summary = self.tracing.summarize_trace_file()
        self.assertIn(self.tracing.SESSION_ID, summary)
        self.assertIn("bash: 1 call(s)", summary)
        self.assertIn("1 blocked", summary)


class _FakeLangfuseSpan:
    """Stand-in for LangfuseSpan/LangfuseGeneration: records calls, mimics nesting."""

    def __init__(self, name, as_type="span", **kwargs):
        self.name = name
        self.as_type = as_type
        self.kwargs = kwargs
        self.children = []
        self.updates = []
        self.end_calls = 0

    def start_observation(self, *, name, as_type="span", **kwargs):
        child = _FakeLangfuseSpan(name, as_type=as_type, **kwargs)
        self.children.append(child)
        return child

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return self

    def end(self, **kwargs):
        self.end_calls += 1
        return self


class _FakeLangfuseClient:
    """Stand-in for the Langfuse() client: only the surface tracing.py calls."""

    def __init__(self):
        self.roots = []

    def start_observation(self, *, name, as_type="span", **kwargs):
        span = _FakeLangfuseSpan(name, as_type=as_type, **kwargs)
        self.roots.append(span)
        return span


class _RaisingLangfuseClient:
    """Stand-in that blows up on every call, to prove tracing() stays resilient."""

    def start_observation(self, **kwargs):
        raise RuntimeError("langfuse is down")


def _fake_propagate_attributes(**kwargs):
    """Stand-in for the real langfuse.propagate_attributes -- a module-level
    function, NOT a Langfuse() instance method (see tracing._propagate_attributes)."""
    return contextlib.nullcontext()


class LangfuseTracingTests(unittest.TestCase):
    """Seams: trace()/trace_llm_call() dual-write to Langfuse; see ADR-0001."""

    def setUp(self):
        import tempfile
        from minicode import config, tracing
        self.tracing = tracing
        self._orig_trace_file = config.TRACE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        config.TRACE_FILE = Path(self._tmp.name) / "trace.jsonl"
        self.config = config
        self._orig_client = tracing._langfuse_client
        self._orig_attempted = tracing._langfuse_init_attempted
        self._orig_propagate = tracing._propagate_attributes
        self._orig_turn = tracing._current_turn
        self._orig_tool_span = tracing._current_tool_span
        tracing._langfuse_init_attempted = True  # skip real construction
        tracing._propagate_attributes = _fake_propagate_attributes
        tracing._current_turn = None
        tracing._current_tool_span = None

    def tearDown(self):
        self.config.TRACE_FILE = self._orig_trace_file
        self._tmp.cleanup()
        self.tracing._langfuse_client = self._orig_client
        self.tracing._langfuse_init_attempted = self._orig_attempted
        self.tracing._propagate_attributes = self._orig_propagate
        self.tracing._current_turn = self._orig_turn
        self.tracing._current_tool_span = self._orig_tool_span

    def test_disabled_when_not_configured_but_jsonl_still_written(self):
        self.tracing._langfuse_client = None
        self.tracing.trace("user_prompt", prompt="hi")
        lines = self.config.TRACE_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)  # JSONL dual-write unaffected

    def test_resilient_to_langfuse_exceptions(self):
        self.tracing._langfuse_client = _RaisingLangfuseClient()
        try:
            self.tracing.trace("user_prompt", prompt="hi")
        except Exception as e:  # pragma: no cover - the point of this test
            self.fail(f"trace() must never raise, got {e!r}")

    def test_turn_boundary_creates_and_ends_root_span(self):
        fake = _FakeLangfuseClient()
        self.tracing._langfuse_client = fake
        self.tracing.trace("user_prompt", prompt="hi")
        self.assertEqual(len(fake.roots), 1)
        self.assertEqual(fake.roots[0].name, "turn")
        self.assertEqual(fake.roots[0].end_calls, 0)
        self.assertIs(self.tracing._current_turn, fake.roots[0])

        self.tracing.trace("turn_end", tool_results=2)
        self.assertEqual(fake.roots[0].end_calls, 1)
        self.assertIsNone(self.tracing._current_turn)

    def test_tool_call_nests_under_turn(self):
        fake = _FakeLangfuseClient()
        self.tracing._langfuse_client = fake
        self.tracing.trace("user_prompt", prompt="hi")
        turn_span = self.tracing._current_turn

        self.tracing.trace("tool_start", tool="bash", input="ls")
        self.assertEqual(len(turn_span.children), 1)
        tool_span = turn_span.children[0]
        self.assertEqual(tool_span.name, "bash")
        self.assertEqual(tool_span.end_calls, 0)
        self.assertIs(self.tracing._current_tool_span, tool_span)

        self.tracing.trace("tool_end", tool="bash", duration_ms=5.0, output_len=2)
        self.assertEqual(tool_span.end_calls, 1)
        self.assertIsNone(self.tracing._current_tool_span)

    def test_llm_call_emits_generation_nested_under_turn(self):
        fake = _FakeLangfuseClient()
        self.tracing._langfuse_client = fake
        self.tracing.trace("user_prompt", prompt="hi")
        turn_span = self.tracing._current_turn

        response = types.SimpleNamespace(
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=100, output_tokens=40))
        self.tracing.trace_llm_call(response, "test-model")

        self.assertEqual(len(turn_span.children), 1)
        generation = turn_span.children[0]
        self.assertEqual(generation.as_type, "generation")
        self.assertEqual(generation.kwargs.get("usage_details"),
                         {"input": 100, "output": 40})
        self.assertEqual(generation.end_calls, 1)

    def test_llm_call_output_includes_response_text(self):
        fake = _FakeLangfuseClient()
        self.tracing._langfuse_client = fake
        self.tracing.trace("user_prompt", prompt="hi")

        response = types.SimpleNamespace(
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=100, output_tokens=40),
            content=[types.SimpleNamespace(type="text", text="Sure, here you go.")])
        self.tracing.trace_llm_call(response, "test-model")

        generation = self.tracing._current_turn.children[0]
        update = generation.updates[-1]
        self.assertEqual(update["output"]["text"], "Sure, here you go.")
        self.assertEqual(update["output"]["stop_reason"], "end_turn")

    def test_llm_call_output_falls_back_to_tool_summary_when_no_text(self):
        fake = _FakeLangfuseClient()
        self.tracing._langfuse_client = fake
        self.tracing.trace("user_prompt", prompt="hi")

        response = types.SimpleNamespace(
            stop_reason="tool_use",
            usage=types.SimpleNamespace(input_tokens=50, output_tokens=10),
            content=[types.SimpleNamespace(type="tool_use", id="t1", name="bash",
                                            input={"command": "ls"})])
        self.tracing.trace_llm_call(response, "test-model")

        generation = self.tracing._current_turn.children[0]
        update = generation.updates[-1]
        self.assertIn("bash", update["output"]["text"])

    def test_llm_call_jsonl_record_includes_output_text(self):
        response = types.SimpleNamespace(
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=5, output_tokens=5),
            content=[types.SimpleNamespace(type="text", text="hello there")])
        self.tracing.trace_llm_call(response, "test-model")
        lines = self.config.TRACE_FILE.read_text(encoding="utf-8").splitlines()
        import json
        rec = json.loads(lines[-1])
        self.assertEqual(rec["output_text"], "hello there")

    def test_real_langfuse_propagate_attributes_is_module_level_not_a_client_method(self):
        """Regression guard: a hand-rolled fake once defined propagate_attributes
        as a method on the fake client, matching a bug in tracing.py rather than
        the real SDK -- every other test here passed while production would have
        raised AttributeError on every turn. Assert the real shape directly."""
        import langfuse
        self.assertTrue(callable(langfuse.propagate_attributes))
        self.assertFalse(hasattr(langfuse.Langfuse, "propagate_attributes"))


class StopHookOutputTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from minicode import config
        self._orig_trace_file = config.TRACE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        config.TRACE_FILE = Path(self._tmp.name) / "trace.jsonl"
        self.config = config

    def tearDown(self):
        self.config.TRACE_FILE = self._orig_trace_file
        self._tmp.cleanup()

    def test_turn_end_includes_final_assistant_response_text(self):
        import json
        from minicode.hooks import stop_hook
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                types.SimpleNamespace(type="text", text="Sure, here you go.")]},
        ]
        stop_hook(messages)
        lines = self.config.TRACE_FILE.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[-1])
        self.assertEqual(rec["event"], "turn_end")
        self.assertEqual(rec["response_text"], "Sure, here you go.")


class CronValidationTests(unittest.TestCase):
    def test_validate_cron(self):
        from minicode.cron import validate_cron
        self.assertIsNone(validate_cron("*/5 * * * *"))
        self.assertIsNotNone(validate_cron("bad cron"))
        self.assertIsNotNone(validate_cron("99 * * * *"))


if __name__ == "__main__":
    unittest.main()

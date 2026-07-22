"""Offline tests for the eval harness and the ablation flags it depends on.

None of these hit the network or the model: they exercise the ablation gating
in the agent (with faked anthropic/dotenv, same trick as
test_minicode_package.py), the harness's pure helpers (task loading, report
rendering), and the task verifiers (run as real subprocesses against known-good
and known-bad solutions).

Run: ``python -m pytest tests/test_evals.py -v``
"""

import importlib
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_fakes():
    """Fake anthropic/dotenv so importing minicode needs no key or network."""
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


class AblationGatingTests(unittest.TestCase):
    """The three flags each remove exactly one capability from the agent."""

    def setUp(self):
        from minicode import config
        self.config = config
        self._orig = (config.ABLATE_MEMORY, config.ABLATE_MULTIAGENT,
                      config.ABLATE_SKILLS)

    def tearDown(self):
        (self.config.ABLATE_MEMORY, self.config.ABLATE_MULTIAGENT,
         self.config.ABLATE_SKILLS) = self._orig

    def test_full_pool_has_multiagent_and_skill_tools(self):
        from minicode.registry import assemble_tool_pool
        self.config.ABLATE_MULTIAGENT = False
        self.config.ABLATE_SKILLS = False
        tools, handlers = assemble_tool_pool()
        names = {t["name"] for t in tools}
        self.assertIn("task", names)
        self.assertIn("load_skill", names)
        self.assertIn("task", handlers)

    def test_ablate_multiagent_drops_multiagent_tools_only(self):
        from minicode.registry import assemble_tool_pool
        self.config.ABLATE_MULTIAGENT = True
        self.config.ABLATE_SKILLS = False
        tools, handlers = assemble_tool_pool()
        names = {t["name"] for t in tools}
        for gone in self.config.MULTIAGENT_TOOLS:
            self.assertNotIn(gone, names)
            self.assertNotIn(gone, handlers)
        self.assertIn("bash", names)       # unrelated tool stays
        self.assertIn("load_skill", names)  # skills untouched

    def test_ablate_skills_drops_load_skill_and_catalog(self):
        from minicode.registry import assemble_tool_pool
        from minicode.skills import assemble_system_prompt
        self.config.ABLATE_SKILLS = True
        self.config.ABLATE_MULTIAGENT = False
        names = {t["name"] for t in assemble_tool_pool()[0]}
        self.assertNotIn("load_skill", names)
        self.assertIn("task", names)  # multi-agent untouched
        prompt = assemble_system_prompt({})
        self.assertNotIn("Skills catalog", prompt)

    def test_skills_catalog_present_when_not_ablated(self):
        from minicode.skills import assemble_system_prompt
        self.config.ABLATE_SKILLS = False
        self.assertIn("Skills catalog", assemble_system_prompt({}))

    def test_ablate_memory_blanks_injected_memories(self):
        from minicode import loop
        self.config.ABLATE_MEMORY = True
        self.assertEqual(loop.update_context({}, []).get("memories"), "")


class AgentLoopMaxRoundsTests(unittest.TestCase):
    """agent_loop stops at max_rounds without ever calling the model."""

    def test_zero_rounds_returns_before_any_llm_call(self):
        from minicode import loop, config

        def boom(*a, **k):
            raise AssertionError("model must not be called at max_rounds=0")

        orig = config.client.messages.create
        config.client.messages.create = boom
        try:
            messages = [{"role": "user", "content": "hi"}]
            loop.agent_loop(messages, {}, max_rounds=0)  # returns immediately
        finally:
            config.client.messages.create = orig


class HarnessHelperTests(unittest.TestCase):
    def test_conditions_are_single_subsystem_ablations(self):
        from evals.harness import CONDITIONS
        self.assertEqual(CONDITIONS["full"], {})
        # every non-full condition sets exactly one MINICODE_ABLATE_* flag
        for name, flags in CONDITIONS.items():
            if name == "full":
                continue
            self.assertEqual(len(flags), 1)
            key = next(iter(flags))
            self.assertTrue(key.startswith("MINICODE_ABLATE_"))

    def test_load_tasks_reads_manifests(self):
        from evals.harness import load_tasks
        ids = {t["id"] for t in load_tasks()}
        self.assertIn("fix-palindrome", ids)
        self.assertIn("implement-anagram", ids)
        for t in load_tasks():
            self.assertIn("prompt", t)
            self.assertTrue((t["_dir"] / "workspace").is_dir())
            self.assertTrue((t["_dir"] / "verify.py").is_file())

    def test_load_tasks_filter(self):
        from evals.harness import load_tasks
        tasks = load_tasks(["fix-palindrome"])
        self.assertEqual([t["id"] for t in tasks], ["fix-palindrome"])

    def test_render_report_aggregates_resolve_rate(self):
        from evals.harness import render_report
        results = [
            {"task": "a", "condition": "full", "solved": True,
             "input_tokens": 100, "output_tokens": 10, "wall_seconds": 1.0},
            {"task": "b", "condition": "full", "solved": False,
             "input_tokens": 200, "output_tokens": 20, "wall_seconds": 3.0},
            {"task": "a", "condition": "no-skills", "solved": False,
             "input_tokens": None, "output_tokens": None, "wall_seconds": None},
        ]
        report = render_report(results)
        self.assertIn("| full | 1/2 | 50% | 150 | 15 | 2 |", report)
        # a condition with only missing metrics still renders, with dashes
        self.assertIn("| no-skills | 0/1 | 0% | - | - | - |", report)


class VerifierTests(unittest.TestCase):
    """The task verifiers actually distinguish correct from incorrect solutions."""

    def _run_verify(self, task_id: str, solution_name: str, source: str) -> int:
        task_dir = REPO_ROOT / "evals" / "tasks" / task_id
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / solution_name).write_text(source, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(task_dir / "verify.py")],
                cwd=tmp, capture_output=True, text=True, timeout=30)
            return proc.returncode

    def test_palindrome_correct_passes_buggy_fails(self):
        good = ("def is_palindrome(s):\n"
                "    c = [ch.lower() for ch in s if ch.isalnum()]\n"
                "    return c == c[::-1]\n")
        bad = ("def is_palindrome(s):\n"
               "    c = [ch.lower() for ch in s if ch.isalnum()]\n"
               "    return c == c\n")
        self.assertEqual(self._run_verify("fix-palindrome", "palindrome.py", good), 0)
        self.assertNotEqual(self._run_verify("fix-palindrome", "palindrome.py", bad), 0)

    def test_anagram_correct_passes_stub_fails(self):
        good = ("def are_anagrams(a, b):\n"
                "    norm = lambda s: sorted(ch.lower() for ch in s if ch.isalnum())\n"
                "    return norm(a) == norm(b)\n")
        stub = ("def are_anagrams(a, b):\n"
                "    raise NotImplementedError\n")
        self.assertEqual(self._run_verify("implement-anagram", "anagram.py", good), 0)
        self.assertNotEqual(self._run_verify("implement-anagram", "anagram.py", stub), 0)


if __name__ == "__main__":
    unittest.main()

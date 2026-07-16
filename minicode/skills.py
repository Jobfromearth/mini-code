"""Skill 加载与系统提示词组装。

Skill 是磁盘上的 ``skills/<name>/SKILL.md``,带 YAML frontmatter。系统提示词
每一轮都从实时 context 重新拼装,让 memory、skill 目录、MCP 状态可见。
副作用:import 时扫描一次 skills 目录。

注意:``assemble_system_prompt`` 需要读取当前已连接的 MCP 服务器,通过
``import ... mcp`` 后访问 ``mcp.mcp_clients``(原地修改的 dict,读取安全)。
"""

from datetime import datetime

import yaml

from . import config
from . import mcp

SKILL_REGISTRY: dict[str, dict] = {}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 ``---`` YAML frontmatter,返回 (元数据 dict, 正文)。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


def scan_skills():
    """(重新)扫描 skills 目录,填充 SKILL_REGISTRY(副作用:读磁盘)。"""
    SKILL_REGISTRY.clear()
    if not config.SKILLS_DIR.exists():
        return
    for directory in sorted(config.SKILLS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        manifest = directory / "SKILL.md"
        if not manifest.exists():
            continue
        raw = manifest.read_text()
        meta, _ = _parse_frontmatter(raw)
        name = meta.get("name", directory.name)
        desc = meta.get("description", raw.split("\n")[0].lstrip("#").strip())
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": desc,
            "content": raw,
        }


scan_skills()


def list_skills() -> str:
    """返回 skill 名称 + 描述的简短清单,用于系统提示词。"""
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(
        f"- {skill['name']}: {skill['description']}"
        for skill in SKILL_REGISTRY.values())


def load_skill(name: str) -> str:
    """返回某个 skill 的完整内容;不存在时返回可用列表提示。"""
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}. Available: {available}"
    return skill["content"]


PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, edit_file, glob, "
             "todo_write, task, load_skill, compact, "
             "create_task, list_tasks, get_task, claim_task, complete_task, "
             "schedule_cron, list_crons, cancel_cron, "
             "spawn_teammate, send_message, check_inbox, "
             "request_shutdown, request_plan, review_plan, "
             "create_worktree, remove_worktree, keep_worktree, "
             "connect_mcp. MCP tools are prefixed mcp__{server}__{tool}.",
    "workspace": f"Working directory: {config.WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    """从实时 context 重新拼装系统提示词。

    每轮 LLM 调用前调用一次,把 memory、skill 目录、MCP 状态、当前时间
    汇入提示词。
    """
    sections = [PROMPT_SECTIONS["identity"],
                PROMPT_SECTIONS["tools"],
                PROMPT_SECTIONS["workspace"]]
    sections.append(f"Current time: {datetime.now().isoformat(timespec='seconds')}")
    sections.append("Skills catalog:\n" + list_skills() +
                    "\nUse load_skill(name) when a skill is relevant.")
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")
    mcp_names = list(mcp.mcp_clients.keys())
    if mcp_names:
        sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")
    return "\n\n".join(sections)

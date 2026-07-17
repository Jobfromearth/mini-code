<div align="center">

# 🤖 minicode

**最小化编码 Agent**

任务 · Worktree · Skills · 团队 · Hooks · 上下文压缩 · Cron · MCP —— 一个 agent 循环,模块化拓展。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg)](pyproject.toml)
[![Tests](https://github.com/Jobfromearth/mini-code/actions/workflows/tests.yml/badge.svg)](https://github.com/Jobfromearth/mini-code/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

[English](README.md) | **中文**

</div>

---

## ✨ 这是什么?

`minicode` 是一个基于 Anthropic API 的最小但完整的编码 Agent。

它用易读的 Python 演示了真实编码 Agent 背后的核心机制:

| 能力 | 模块 | 要点 |
|---|---|---|
| 🔧 工具分发 | `tools.py` `registry.py` | schema 与处理器分离;模型看 schema,Python 执行处理器 |
| 🛡️ 权限 Hook | `hooks.py` | 拒绝列表、破坏性命令确认、路径越界防护 |
| 📝 Todo 跟踪 | `tools.py` | 会话级 todo,严格校验输入(绝不 `eval`) |
| 🤏 子 Agent | `subagent.py` | 专注的子 agent,只返回最终摘要 |
| 📚 Skills | `skills.py` | 基于 frontmatter 的 skill 目录,注入系统提示词 |
| 🗜️ 上下文压缩 | `compaction.py` | 分层预算:落盘 → 裁剪 → 微压缩 → 摘要 |
| 🔁 错误恢复 | `recovery.py` | 带抖动的退避重试、429/529 处理、模型回退 |
| 🗂️ 任务图 | `tasks.py` | 文件持久化任务,支持 `blockedBy` 依赖 |
| ⏳ 后台任务 | `background.py` | 慢工具先返回占位,结果以通知形式回注 |
| ⏰ Cron 调度 | `cron.py` | 可持久化的定时 prompt,回注进同一循环 |
| 👥 团队协作 | `teams.py` `bus.py` | 自主线程、JSONL 邮箱、plan 审批协议 |
| 🌲 Git worktree | `worktrees.py` | 与任务绑定的隔离工作目录 |
| 🔌 MCP | `mcp.py` | 晚绑定的外部工具,并入统一工具池 |
| 📊 行为追踪 | `tracing.py` | JSONL 事件日志,双写进自托管的 [Langfuse](https://langfuse.com) 可视化界面:token 用量、工具耗时、权限拦截 |

## 🚀 快速开始

```bash
# 1. 安装(可编辑模式)
pip install -e .

# 2. 配置 —— 复制示例文件并填入你的密钥
cp .env.example .env
#    必填:ANTHROPIC_API_KEY、MODEL_ID

# 3. 运行
minicode            # console script
python -m minicode  # 或作为模块运行
python legacy_entry.py  # 旧入口(向后兼容薄壳)
```

在 `minicode >>` 提示符下输入问题;输入 `q` 退出。

## 🏗️ 架构

依赖方向严格向下 —— 模块只能 import 它下方的层:

```
┌────────────────────────────────────────────────┐
│  __main__          CLI 入口                     │
├────────────────────────────────────────────────┤
│  loop              agent 主循环                 │
├────────────────────────────────────────────────┤
│  registry          工具 schema + 处理器表        │
├────────────────────────────────────────────────┤
│  teams             自主 teammate                │
│                    (自带 mini 循环,不依赖 loop) │
├────────────────────────────────────────────────┤
│  subagent  background                          │
├────────────────────────────────────────────────┤
│  tools  hooks                                  │
├────────────────────────────────────────────────┤
│  tasks  worktrees  skills  bus  mcp            │
│  recovery  compaction  cron                    │
├────────────────────────────────────────────────┤
│  terminal  content                             │
├────────────────────────────────────────────────┤
│  config            env、client、常量            │
└────────────────────────────────────────────────┘
```

## 📊 可观测性

每个事件(大模型调用、工具开始/结束、权限拒绝、压缩、cron 注入)都会追加写入本地 JSONL trace 文件;配置好后,还会双写进一个自托管的 [Langfuse](https://langfuse.com) 实例——一个对话 **Turn** 对应一条 Langfuse trace,工具调用和大模型生成都嵌套在下面,展示的是模型真实的输出内容,而不只是计数类的记账信息。

```bash
# 拉起自托管 Langfuse(Postgres + ClickHouse + Redis + MinIO)
cd deploy/langfuse
cp .env.example .env
docker compose up -d          # 等待约 2-3 分钟后访问 http://localhost:3000

# 让 minicode 连上它(写进仓库根目录的 .env)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000
```

Langfuse 完全是可选、纯增量的:不配置或连不上时,追踪会静默降级为只写 JSONL——完整的设计取舍见 [ADR-0001](docs/adr/0001-self-hosted-langfuse-tracing.md)(为什么自托管、为什么按 turn 粒度、为什么双写)。

## 📂 项目结构

```
minicode/            包本体(见上表)
legacy_entry.py      向后兼容薄壳:`python legacy_entry.py` 仍可用
                     (改这个名字是为了不和标准库的 `code` 模块撞名——它以前
                     就叫 code.py,在没有 .env 的干净环境里会悄悄弄崩
                     pytest/CI,详见 git 历史)
skills/              skill 目录(带 YAML frontmatter 的 SKILL.md)
deploy/langfuse/     自托管 Langfuse 的 docker-compose 栈
docs/adr/            架构决策记录
docs/agents/         工程类 skills 用到的 issue tracker / 分类标签约定
CONTEXT.md           领域术语表(Session 与 Turn 的区分等)
tests/               pytest 测试套件
pyproject.toml       打包配置 + `minicode` console script
```

运行时状态存放在按需创建的点目录中:`.tasks/`、`.worktrees/`、`.mailboxes/`、`.transcripts/`、`.memory/`、`.scheduled_tasks.json`、`.traces/`。

## 🧪 测试

```bash
pip install -e ".[test]"
python -m pytest tests/ -v
```

每次 push/PR 都会通过 [GitHub Actions](.github/workflows/tests.yml) 自动跑一遍(Python 3.10 和 3.12)。测试覆盖:导入完整性(无循环导入)、todo 校验与注入安全、压缩时保持 `tool_use`/`tool_result` 成对、后台任务判定、cron 表达式校验,以及 Langfuse 双写行为——包括 Langfuse 连不上或没配置时,追踪逻辑绝不会抛异常。

## 📄 许可

[MIT](LICENSE)

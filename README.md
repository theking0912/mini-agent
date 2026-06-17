<div align="center">

# 🤖 Mini Agent

**从零搭建的最小化 AI Agent 框架**

[![CI](https://github.com/theking0912/mini-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/theking0912/mini-agent/actions/workflows/ci.yml)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github)](https://codespaces.new/theking0912/mini-agent)
[![Python](https://img.shields.io/badge/Python-3.13+-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**695 行纯 Python · 无 AI 框架依赖 · 展示 Tool Calling 核心执行原理**

</div>

---

## 项目结构

```
mini-agent/
├── agent.py              ← 入口（交互式 REPL / 单次查询）
├── core/
│   ├── llm.py            ← LLM 通信层（httpx 裸调 API，不依赖 SDK）
│   ├── context.py        ← 对话上下文管理（消息历史 + 自动裁剪）
│   └── tool_runner.py    ← 核心执行引擎（Tool Calling Loop）
├── tools/
│   ├── registry.py       ← 工具注册中心（schema 生成 + 分发执行）
│   ├── calculator.py     ← 计算器（AST 安全求值，不用 eval）
│   ├── file_tool.py      ← 文件读取（安全路径校验）
│   └── web_search.py     ← 网络搜索（DuckDuckGo，无需 API Key）
├── scripts/
│   └── test_imports.py   ← CI 导入测试
├── .devcontainer/        ← GitHub Codespaces 配置
└── .github/workflows/    ← CI 自动检查
```

## 核心执行流程

```
用户输入
    ↓
 1. chat(messages + tools) → LLM 返回 tool_calls 或文本
    ↓                    ↙
 2. 如果有 tool_calls ──→ 逐个执行工具 → 结果写回 messages[tool role]
    ↓                     ↓
 3. 回到步骤 1，带工具结果再次调 LLM
    ↓
 4. LLM 返回纯文本 → 输出给用户 ✅
```

## 快速开始

### 方式一：GitHub Codespaces（推荐，浏览器即用）

[![Open in Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/theking0912/mini-agent)

点击上方按钮 → 等环境加载完 → 终端里直接运行：

```bash
export OPENAI_API_KEY='你的API_KEY'
export OPENAI_BASE_URL='https://api.deepseek.com/v1'
export LLM_MODEL='deepseek-chat'

python agent.py
```

### 方式二：本地运行

```bash
git clone https://github.com/theking0912/mini-agent.git
cd mini-agent
pip install httpx duckduckgo-search

# 交互模式
export OPENAI_API_KEY='你的API_KEY'
python agent.py

# 或单次查询
python agent.py "计算 2 的 10 次方"
```

## 交互命令

进入 REPL 后支持：

| 命令 | 说明 |
|------|------|
| `/tools` | 列出所有可用工具 |
| `/reset` | 重置对话 |
| `/debug` | 查看当前上下文消息 |
| `/help` | 显示帮助 |
| `/quit` | 退出 |

## 学习路线

学完 V1 建议继续：

| 版本 | 内容 | 学到什么 |
|------|------|----------|
| **V1 ✅** | 695 行最小 Agent + 3 个工具 | Tool Calling 原理、API 协议、上下文管理 |
| **V2** | SQLite 持久化记忆 | 长期记忆、会话管理、向量检索 |
| **V3** | 工具链编排 | 多步推理、任务分解、结果聚合 |
| **V4** | MCP 协议支持 | 热插拔工具、标准化协议、动态注册 |

## License

MIT

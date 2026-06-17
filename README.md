# 🤖 Mini Agent — 从零搭建的 AI Agent

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/theking0912/mini-agent)

一个最小化的 AI Agent 框架，展示 Tool Calling 核心执行原理。

全部代码 **695 行纯 Python**，零 AI 框架依赖，只用 httpx 裸调 HTTP API。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 设置 API Key（看你用哪个模型）
export DEEPSEEK_API_KEY='sk-xxx'
export OPENAI_API_KEY='sk-xxx'

# 交互模式
python agent.py

# 单次查询
python agent.py "计算 2 的 10 次方"

# 指定模型
python agent.py --model gpt4o-mini "搜索 AI 新闻"
```

## 多模型配置

支持多个 LLM Provider 共存，每个模型独立配置 API Key。

配置文件：`config/models.json`

```json
{
  "default": "deepseek",
  "models": {
    "deepseek": {
      "api_key": "${DEEPSEEK_API_KEY}",
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "description": "DeepSeek V3"
    },
    "gpt4o": {
      "api_key": "${OPENAI_API_KEY}",
      "base_url": "https://api.openai.com/v1",
      "model": "gpt-4o",
      "description": "OpenAI GPT-4o"
    }
  }
}
```

### Key 管理原则

- **API Key 不进配置文件** — 使用 `${ENV_VAR_NAME}` 语法引用环境变量
- **运行时插值** — 启动时从环境变量读取实际的 Key
- **不同模型可以共用同一个 Key**（如 DeepSeek 系列共用 DEEPSEEK_API_KEY）
- **无配置文件时自动回退**到传统的 `OPENAI_API_KEY` 环境变量

### 交互式模型切换

在交互式 REPL 中：

```
🙋 > /model

📡 可用模型：
   deepseek          DeepSeek V3                   [deepseek-chat] ✅
   gpt4o             OpenAI GPT-4o                 [gpt-4o]        ✅ ◀
   gpt4o-mini        OpenAI GPT-4o Mini            [gpt-4o-mini]   ✅

  输入 /model NAME 切换
  输入 /reload 重新加载配置文件

🙋 > /model deepseek
  🔄 已切换: gpt4o → deepseek (DeepSeek V3) [deepseek-chat]
```

## 项目结构

```
mini-agent/
├── agent.py              ← 入口（交互式 REPL + 命令行）
├── requirements.txt      ← 唯一依赖：httpx
├── config/
│   └── models.json       ← 多模型配置文件
├── core/
│   ├── llm.py            ← LLM API 通信层（HTTP 原始调用）
│   ├── config.py         ← 模型配置加载器（支持多 Key / 多 Provider）
│   ├── context.py        ← 对话上下文管理
│   └── tool_runner.py    ← 工具调用环（核心执行引擎）
└── tools/
    ├── registry.py       ← 工具注册中心
    ├── calculator.py     ← 计算器工具
    ├── file_tool.py      ← 文件读取工具
    └── web_search.py     ← 网络搜索工具
```

## 架构演进

| 版本 | 新增 |
|------|------|
| v1 | 调 API，能调用 2-3 个工具 |
| v2 | 记忆（SQLite 上下文保存） |
| v3 | 多轮对话 + 工具链编排 |
| v4 | **MCP 协议支持（动态注册工具）** |
| v5 | **多模型 Key 适配 + 运行时切换** |

## 进程

```plaintext
用户输入 → LLM(理解意图) → 调用工具 → LLM(组织回复) → 输出
                ↑                        |
                └──── 工具结果回流 ────────┘
```

## 许可证

MIT

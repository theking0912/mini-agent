# 🤖 Mini Agent — 从零搭建的 AI Agent

[![GitHub stars](https://img.shields.io/github/stars/theking0912/mini-agent)](https://github.com/theking0912/mini-agent/stargazers)
[![License](https://img.shields.io/github/license/theking0912/mini-agent)](https://github.com/theking0912/mini-agent/blob/main/LICENSE)

一个最小化的 AI Agent 框架 + Web UI，展示 Tool Calling 核心执行原理。

**核心**: ~700 行纯 Python，零 AI 框架依赖，只用 httpx 裸调 HTTP API。
**Web UI**: FastAPI + PostgreSQL + MinIO，支持多用户、多模型、流式对话。

---

## 特性

- **多模型支持** — DeepSeek、OpenAI 等任意 OpenAI 兼容 API，运行时热切换
- **多用户系统** — 邮箱注册/登录，每人独立管理 API Key（加密存储）
- **流式对话** — SSE 实时流式输出，打字机效果
- **工具调用** — 计算器、文件读取、Web 搜索，LLM 自主决策调用
- **用户头像** — MinIO 存储，上传/裁剪/实时刷新
- **Docker 部署** — 一键启动，PostgreSQL + Redis + MinIO 三件套
- **CLI + Web 双模式** — 终端老兵和 Web 新手都能用

---

## 快速开始

### CLI 模式（纯 Python）

```bash
# 安装依赖
pip install -r requirements.txt

# 设置 API Key
export DEEPSEEK_API_KEY='sk-xxx'
export OPENAI_API_KEY='sk-xxx'

# 交互式 REPL
python agent.py

# 单次查询
python agent.py "计算 2 的 10 次方"

# 指定模型
python agent.py --model gpt4o-mini "搜索 AI 新闻"
```

### Web UI（Docker 部署）

```bash
# 需要 PostgreSQL + Redis + MinIO，见下方依赖说明
docker build -t mini-agent:latest .
docker run -d \
  --name mini-agent \
  --restart unless-stopped \
  -p 8080:8080 \
  -v mini-agent-data:/data \
  mini-agent:latest
```

访问 `http://localhost:8080` → 注册账号 → 在设置中添加 API Key → 开始聊天。

---

## 依赖服务

### PostgreSQL（用户数据 + 配置）

```bash
docker run -d \
  --name mini-agent-pg \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=LeroyLee \
  -e POSTGRES_DB=mini_agent \
  -p 5433:5432 \
  postgres:16
```

### Redis（验证码缓存）

```bash
docker run -d \
  --name mini-agent-redis \
  --restart unless-stopped \
  -p 6379:6379 \
  redis:7
```

### MinIO（头像存储）

```bash
docker run -d \
  --name mini-agent-minio \
  --restart unless-stopped \
  -e MINIO_ROOT_USER=leroy \
  -e MINIO_ROOT_PASSWORD=<your-password> \
  -p 9000:9000 -p 9001:9001 \
  minio/minio server /data --console-address ":9001"
```

---

## 配置

### 数据库 & 服务连接

`config/db.json`:

```json
{
  "pg_host": "172.18.0.1",
  "pg_port": 5433,
  "pg_db": "mini_agent",
  "pg_user": "postgres",
  "pg_password": "LeroyLee",
  "redis_host": "172.18.0.1",
  "redis_port": 6379
}
```

### 多模型配置

`config/models.json` — 支持任意 OpenAI 兼容 API：

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

Key 管理原则：
- **API Key 不进配置文件** — `${ENV_VAR_NAME}` 语法引用环境变量
- **Web UI 中每个用户独立管理 Key** — 加密存入 PostgreSQL JSONB 字段
- **运行时热切换** — 聊天时随时切换模型，无需重启

---

## 项目结构

```
mini-agent/
├── agent.py                  ← CLI 入口（交互式 REPL + 命令行）
├── server.py                 ← Web UI 服务端（FastAPI）
├── requirements.txt          ← Python 依赖
├── Dockerfile                ← Docker 镜像构建
├── config/
│   ├── models.json           ← 多模型配置文件
│   └── db.json               ← 数据库连接配置
├── core/
│   ├── llm.py                ← LLM API 通信层
│   ├── config.py             ← 模型配置加载器
│   ├── context.py            ← 对话上下文管理
│   ├── db.py                 ← PostgreSQL + Redis 连接池
│   ├── user.py               ← 用户注册/登录/API Key 管理
│   ├── email.py              ← 邮件发送（邮箱验证码）
│   └── verify.py             ← 验证码生成/校验
├── tools/
│   ├── registry.py           ← 工具注册中心
│   ├── calculator.py         ← 计算器工具
│   ├── file_tool.py          ← 文件读取工具
│   └── web_search.py         ← 网络搜索工具
└── web/
    ├── index.html            ← Web UI 主界面（聊天 + 设置）
    └── auth.html             ← 注册/登录页
```

---

## 架构

### 执行流程

```plaintext
用户输入 ─→ LLM(理解意图) ─→ 调用工具 ─→ LLM(组织回复) ─→ 输出
                  ↑                              │
                  └────── 工具结果回流 ────────────┘
```

### Web UI 架构

```plaintext
浏览器 ←SSE→ FastAPI Server ←HTTP→ LLM API
                │
          ┌─────┼─────┐
          │     │     │
      PostgreSQL Redis MinIO
      (用户/Key)  (验证码) (头像)
```

---

## API 概览

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web UI 主页面 |
| `/auth` | GET | 注册/登录页 |
| `/api/auth/register` | POST | 注册（发送验证码） |
| `/api/auth/verify` | POST | 验证邮箱 |
| `/api/auth/login` | POST | 登录 |
| `/api/auth/me` | GET | 当前用户信息 |
| `/api/auth/logout` | POST | 退出登录 |
| `/api/models` | GET | 可用模型列表 |
| `/api/switch` | POST | 切换模型 |
| `/api/chat` | POST | 发送消息（SSE 流式响应） |
| `/api/reset` | POST | 重置对话上下文 |
| `/api/key/set` | POST | 保存 API Key |
| `/api/key/remove` | POST | 删除 API Key |
| `/api/key/list` | GET | 已保存的 Key 列表 |
| `/api/avatar/upload` | POST | 上传头像 |
| `/api/avatar/{id}` | GET | 获取头像 |

---

## 架构演进

| 版本 | 新增 |
|------|------|
| v1 | 调 API，能调用 2-3 个工具 |
| v2 | 记忆（SQLite 上下文保存） |
| v3 | 多轮对话 + 工具链编排 |
| v4 | MCP 协议支持（动态注册工具） |
| v5 | **多模型 Key 适配 + 运行时切换** |
| v6 | **Web UI + 多用户 + 头像管理** |

---

---

# 二、代码架构分析

## 调用流程图

### CLI 模式（`agent.py`）

```mermaid
flowchart TB
    U[🙋 用户输入] --> A[agent.py]
    A --> C{参数解析}
    C -->|无参数| I[interactive_mode]
    C -->|有 query| S[single_query]
    
    I --> CFG[core.config.get_config]
    S --> CFG
    CFG --> MD[config/models.json]
    CFG --> CTX[core.context.Context]
    
    CTX --> RUN[core.tool_runner.run_agent]
    RUN --> REG[tools.registry.get_schemas]
    REG --> CAL[tools.calculator]
    REG --> WEB[tools.web_search]
    REG --> FIL[tools.file_tool]
    
    RUN --> LLM[core.llm.chat]
    LLM --> API[LLM API /v1/chat/completions]
    
    RUN --> LOOP{LLM 返回}
    LOOP -->|tool_calls| EXEC[tools.registry.execute]
    EXEC --> CTX2[context.add_tool_result]
    CTX2 --> LLM
    
    LOOP -->|文本回复| OUT[🤖 最终输出]
```

### Web UI 模式（`server.py`）

```mermaid
flowchart TB
    B[浏览器] -->|GET /| S[server.py FastAPI]
    B -->|GET /auth| AUTH[web/auth.html]
    B -->|POST /api/*| API[API 路由]
    
    subgraph FastAPI 服务
        S
        API
        DB[core.db]
        USER[core.user]
        VER[core.verify]
        EMAIL[core.email]
    end
    
    subgraph 外部服务
        PG[(PostgreSQL)]
        RD[(Redis)]
        MI[(MinIO)]
        LLM_API[LLM API]
    end
    
    S -->|startup| DB
    DB --> PG
    DB --> RD
    
    subgraph 注册登录流程
        API -->|/api/auth/register| VER
        API -->|/api/auth/register| EMAIL
        VER --> RD
        API -->|/api/auth/verify| USER
        USER --> PG
        API -->|/api/auth/login| USER
        USER --> PG
    end
    
    subgraph 聊天流程
        API -->|/api/chat| CFG[core.config.get_config]
        CFG --> MD[config/models.json]
        API -->|获取用户 Key| USER
        API --> SSE[SSE _stream_chat]
        SSE --> CTX[core.context.Context]
        SSE --> LLM[core.llm.chat]
        LLM --> LLM_API
        SSE --> REG[tools.registry.execute]
        REG --> TOOLS[tools.calculator/web_search/file_tool]
    end
    
    subgraph 头像流程
        API -->|/api/avatar/upload| MI
        API -->|/api/avatar/:id| MI
        API -->|记录路径| USER
        USER --> PG
    end
```

### Agent 核心循环（Tool Calling Loop）

```mermaid
sequenceDiagram
    actor U as 用户
    participant A as agent.py / server.py
    participant C as core.context
    participant L as core.llm
    participant R as tools.registry
    participant T as 具体工具

    U->>A: "计算 2**10，然后搜索 AI 新闻"
    A->>C: add_user("计算 2**10...")
    A->>L: chat(messages, tools)
    L->>L: POST /v1/chat/completions
    L-->>A: tool_calls=[calculator, web_search]
    
    Note over A,R: 第一轮：LLM 决定调用两个工具
    
    A->>C: add_assistant(tool_calls=[...])
    
    par 并行调用工具
        A->>R: execute("calculator", {expr: "2**10"})
        R->>T: calculator({expr: "2**10"})
        T-->>R: "2**10 = 1024"
        R-->>A: "2**10 = 1024"
        A->>C: add_tool_result(tool_call_id, result)
    and
        A->>R: execute("web_search", {query: "AI 新闻"})
        R->>T: web_search({query: "AI 新闻"})
        T->>T: DuckDuckGo API
        T-->>R: 搜索结果列表
        R-->>A: 搜索结果
        A->>C: add_tool_result(tool_call_id, result)
    end
    
    A->>L: chat(messages + 工具结果, tools)
    L->>L: POST /v1/chat/completions
    L-->>A: content="计算结果是 1024...AI 新闻..."
    
    Note over A,R: 第二轮：LLM 根据工具结果生成回复
    
    A->>C: add_assistant(content="计算结果是 1024...")
    A-->>U: 🤖 1024，关于 AI 新闻...
```

### 多模型 Key 适配体系

```mermaid
flowchart LR
    subgraph 配置层
        MJ[config/models.json]
        DJ[config/db.json]
    end
    
    subgraph Key 来源
        EV[环境变量\n${DEEPSEEK_API_KEY}]
        KR[core.keyring\n加密文件 keys.enc]
        UK[core.user\n用户独立 Key\nPostgreSQL JSONB]
    end
    
    subgraph 解析层
        CP[core.config\n_resolve_api_key]
    end
    
    subgraph 最终用户
        CLI[agent.py CLI]
        WEB[server.py Web]
    end
    
    MJ --> CP
    EV --> CP
    KR --> CP
    DJ --> DB[core.db]
    DB --> UK
    
    CP --> CLI
    UK --> WEB
    
    style EV fill:#2d5a27
    style KR fill:#5a3d2d
    style UK fill:#2d3d5a
```

---

## 文件依赖关系

```mermaid
flowchart TB
    subgraph "入口层"
        AG[agent.py]
        SV[server.py]
    end
    
    subgraph "核心层 core/"
        CFG[config.py]
        CTX[context.py]
        LLM[llm.py]
        TR[tool_runner.py]
        KR[keyring.py]
        DB[db.py]
        UR[user.py]
        VR[verify.py]
        EM[email.py]
    end
    
    subgraph "工具层 tools/"
        TI[tools/__init__.py]
        RG[registry.py]
        CA[calculator.py]
        FI[file_tool.py]
        WE[web_search.py]
    end
    
    subgraph "数据层"
        MJ[config/models.json]
        DJ[config/db.json]
        PG[(PostgreSQL)]
        RD[(Redis)]
        MI[(MinIO)]
    end
    
    AG --> CFG
    AG --> CTX
    AG --> TR
    AG --> KR
    
    SV --> CFG
    SV --> CTX
    SV --> LLM
    SV --> DB
    SV --> UR
    SV --> VR
    SV --> EM
    
    TR --> LLM
    TR --> CTX
    TR --> RG
    
    LLM --> CFG
    
    KR --- CFG
    
    DB --> DJ
    DB --> PG
    DB --> RD
    
    UR --> DB
    
    VR --> RD
    EM --> DJ
    
    TI --> CA
    TI --> FI
    TI --> WE
    
    CA --> RG
    FI --> RG
    WE --> RG
    
    CFG --> MJ
```

---

## 各文件关键数据

| 文件 | 行数 | 关键数据结构 | 核心函数 |
|------|------|-------------|---------|
| `agent.py` | 302 | `Context`, `AppConfig` | `interactive_mode()`, `single_query()` |
| `server.py` | 563 | `_context`(全局), FastAPI routes | `_stream_chat()`, `_minio_put()` |
| `core/config.py` | 234 | `AppConfig`, `ModelConfig` | `load_config()`, `_resolve_api_key()` |
| `core/context.py` | 101 | `Context.messages: list[dict]` | `add_user()`, `add_tool_result()` |
| `core/llm.py` | 148 | `LLMResponse` | `chat()` |
| `core/tool_runner.py` | 83 | (无，纯函数) | `run_agent()` |
| `core/keyring.py` | 190 | `keys.enc` (Fernet 加密 JSON) | `save_key()`, `load_key()` |
| `core/db.py` | 131 | 连接池 + Redis 客户端 | `get_conn()`, `init_db()` |
| `core/user.py` | 239 | PostgreSQL `users` 表 | `create_user()`, `login_user()`, `set_user_key()` |
| `core/verify.py` | 47 | Redis `verify:register:email` | `generate_code()`, `verify_code()` |
| `core/email.py` | 149 | SMTP 连接 | `send_verification_code()` |
| `tools/registry.py` | 74 | `_registry: dict[str, dict]` | `register()`, `execute()` |
| `tools/calculator.py` | 76 | AST 白名单 | `_safe_eval()` |
| `tools/web_search.py` | 54 | DuckDuckGo 搜索 | `_search_ddg()` |
| `tools/file_tool.py` | 50 | 路径黑名单 | `read_file()` |
| `web/index.html` | 1041 | CSS + JS 内联单页 | SSE 客户端, 模型状态管理 |
| `web/auth.html` | 398 | 登录/注册切换 | 验证码倒计时, 表单验证 |

---

## 代码 Review 总结

| 等级 | 问题 | 文件 | 影响 |
|------|------|------|------|
| 🔴 严重 | 全局 `_context` 所有用户共享 | `server.py:32` | 多用户串对话 |
| 🔴 严重 | `user.py` 每次新建 DB 连接（未用池子） | `core/user.py` | 高并发连接耗尽 |
| 🔴 严重 | `llm.py` 用同步 `httpx.Client` | `core/llm.py:99` | 阻塞 FastAPI 事件循环 |
| 🔴 严重 | 文件读取黑名单太弱（白名单更好） | `tools/file_tool.py:34` | 敏感文件泄露风险 |
| 🟠 架构 | 双份 Agent 循环代码（CLI + Web 各一套） | `tool_runner.py` + `server.py` | 维护成本翻倍 |
| 🟠 架构 | SSE "打字效果"是虚假流式（先等全响应再逐词吐出） | `server.py:521` | 用户体验差 |
| 🟠 架构 | `email.py` 自己读 `db.json`，不与 `db.py` 复用 | `core/email.py:16` | 配置加载不统一 |
| 🟡 整洁 | 100 行 AWS Signature V4 实现在 `server.py` 内联 | `server.py:50-172` | 应提到 `core/storage.py` |
| 🟡 整洁 | import 散落在函数内部（`from core import llm`） | `server.py:498` | 风格不一致 |
| 🟡 整洁 | `calculator.py` docstring 残缺 "计...器" | `tools/calculator.py:3` | 排版瑕疵 |
| 🟢 细节 | 上下文 token 估算不准（中文偏差大） | `core/context.py:82` | 中文用户可能过早裁剪 |

---

## 许可证

MIT

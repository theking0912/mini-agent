# Mini Agent

从零搭一个最小化 AI Agent，理解大模型底层的 tool calling 原理。

## 路线图

| 版本 | 内容 |
|------|------|
| **v1** | 单 Python 脚本 + LLM API + 3 个工具（计算器/读文件/搜索），跑通 tool calling |
| **v2** | 加入 SQLite 记忆层，支持多轮上下文 |
| **v3** | 工具链编排（Agent 自己决定工具的先后顺序） |
| **v4** | MCP 协议支持，外部工具动态注册 |

## 启动（v1）

```bash
cd /vol1/1000/dev/project/mini-agent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配置 API Key
export LLM_API_KEY="your-api-key"

# 运行
python agent.py
```

## 结构

```
mini-agent/
├── agent.py          # 主循环：接收输入 → 调 LLM → 执行工具 → 输出
├── llm.py            # LLM API 调用封装
├── tool_parser.py    # 解析 tool_call，分发给对应工具
├── config.py         # 配置管理
├── tools/
│   ├── calculator.py # 计算器工具
│   ├── file_reader.py# 读文件工具
│   └── web_search.py # 搜索工具
├── requirements.txt
└── README.md
```

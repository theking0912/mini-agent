#!/usr/bin/env python3
"""
Mini Agent — 从零搭建的最小化 AI Agent 框架
===========================================
用法：
    # 交互式（推荐）
    python agent.py

    # 单次查询
    python agent.py "计算 2 的 10 次方"

项目结构：
    agent.py              ← 入口（你在这里）
    core/
        llm.py            ← LLM API 通信层（HTTP 原始调用）
        context.py         ← 对话上下文管理
        tool_runner.py     ← 工具调用环（核心执行引擎）
    tools/
        registry.py        ← 工具注册中心
        calculator.py      ← 计算器工具
        file_tool.py       ← 文件读取工具
        web_search.py      ← 网络搜索工具
"""
import os
import sys
from core.context import Context
from core.tool_runner import run_agent
from tools import registry


def interactive_mode():
    """交互式 REPL"""
    print("=" * 60)
    print("  🤖 Mini Agent — 从零搭建的 AI Agent")
    print("=" * 60)
    tool_names = ", ".join(r for r in registry._registry)
    print(f"  已加载工具: {tool_names}")
    api_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    print(f"  API: {api_url}")
    model = os.environ.get("LLM_MODEL", "gpt-4o")
    print(f"  Model: {model}")
    print("  输入 /help 查看命令, /quit 退出")
    print()

    context = Context()

    while True:
        try:
            user_input = input("🙋 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue
        if user_input == "/quit":
            print("👋 再见！")
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/tools":
            print(registry.list_tools())
            continue
        if user_input == "/reset":
            context.reset()
            print("🔄 对话已重置")
            continue
        if user_input == "/debug":
            _show_context(context)
            continue

        try:
            print("\n🤖 思考中...")
            reply = run_agent(context, user_input)
            print(f"\n🤖 {reply}\n")
        except Exception as e:
            print(f"\n❌ 错误: {e}")


def single_query(query: str):
    """单次查询模式"""
    context = Context()
    reply = run_agent(context, query)
    print(reply)


def _show_help():
    print("""
  命令:
    /help     — 显示此帮助
    /tools    — 列出所有可用工具
    /reset    — 重置对话
    /debug    — 显示当前上下文
    /quit     — 退出
    """.strip())


def _show_context(context):
    print("\n📋 当前上下文消息数:", len(context.get_messages()))
    for i, m in enumerate(context.get_messages()):
        role = m["role"]
        content = str(m.get("content", ""))[:80]
        has_tc = "tool_calls" in m
        tc_text = " (有工具调用)" if has_tc else ""
        print(f"  [{i}] {role}: {content}{tc_text}")
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        single_query(" ".join(sys.argv[1:]))
    else:
        interactive_mode()

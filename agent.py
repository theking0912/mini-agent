#!/usr/bin/env python3
"""
Mini Agent — 从零搭建的最小化 AI Agent 框架
===========================================
用法：
    # 交互式（推荐）
    python agent.py

    # 单次查询（使用默认模型）
    python agent.py "计算 2 的 10 次方"

    # 单次查询（指定模型）
    python agent.py --model deepseek "搜索最新的 AI 新闻"

项目结构：
    agent.py              ← 入口（你在这里）
    config/
        models.json       ← 多模型配置文件
    core/
        llm.py            ← LLM API 通信层（HTTP 原始调用）
        config.py         ← 模型配置加载器（支持多 Key / 多 Provider）
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
import argparse
from core.context import Context
from core.tool_runner import run_agent
from tools import registry
from core.config import get_config, reload_config


def interactive_mode(initial_model: str | None = None):
    """交互式 REPL"""
    cfg = get_config()

    # 如果指定了初始模型，切换到它
    if initial_model and initial_model in cfg.models:
        cfg.switch(initial_model)

    print("=" * 60)
    print("  🤖 Mini Agent — 从零搭建的 AI Agent")
    print("=" * 60)
    tool_names = ", ".join(r for r in registry._registry)
    print(f"  已加载工具: {tool_names}")

    current = cfg.current_model
    print(f"  当前模型: {current.name} ({current.description})")
    print(f"  API: {current.base_url} | Model: {current.model}")
    print(f"  可用模型: {', '.join(cfg.models.keys())}")
    print("  输入 /model 切换模型, /help 查看命令, /quit 退出")
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
        if user_input == "/model":
            _show_model_menu(cfg)
            continue
        if user_input.startswith("/model "):
            name = user_input[7:].strip()
            _switch_model(cfg, name)
            continue
        if user_input == "/reload":
            reload_config()
            _show_model_menu(get_config())
            continue

        try:
            print("\n🤖 思考中...")
            reply = run_agent(context, user_input)
            print(f"\n🤖 {reply}\n")
        except Exception as e:
            print(f"\n❌ 错误: {e}\n")


def single_query(query: str, model_name: str | None = None):
    """单次查询模式"""
    cfg = get_config()
    if model_name and model_name in cfg.models:
        cfg.switch(model_name)

    context = Context()
    reply = run_agent(context, query)
    print(reply)


def _show_help():
    print("""
  命令:
    /help        — 显示此帮助
    /tools       — 列出所有可用工具
    /model       — 列出并切换模型
    /model NAME  — 直接切换到指定模型（例如 /model deepseek）
    /reload      — 重新加载 config/models.json
    /reset       — 重置对话
    /debug       — 显示当前上下文
    /quit        — 退出
    """ .strip())


def _show_context(context):
    print("\n📋 当前上下文消息数:", len(context.get_messages()))
    for i, m in enumerate(context.get_messages()):
        role = m["role"]
        content = str(m.get("content", ""))[:80]
        has_tc = "tool_calls" in m
        tc_text = " (有工具调用)" if has_tc else ""
        print(f"  [{i}] {role}: {content}{tc_text}")
    print()


def _show_model_menu(cfg):
    """显示模型切换界面"""
    current = cfg.current_model.name
    print("\n📡 可用模型：")
    for name, m in cfg.models.items():
        marker = " ◀" if name == current else ""
        key_status = "✅" if m.api_key else "❌ 无 Key"
        print(f"  {'▶' if name == current else ' '} {name:20s} {m.description:25s} [{m.model}] {key_status}{marker}")
    print()
    print(f"  输入 /model NAME 切换（例如 /model deepseek）")
    print(f"  输入 /reload 重新加载配置文件")
    print()


def _switch_model(cfg, name: str):
    """切换模型"""
    if name not in cfg.models:
        print(f"❌ 未知模型: '{name}'")
        _show_model_menu(cfg)
        return
    msg = cfg.switch(name)
    print(f"  {msg}")
    # 检查 Key
    m = cfg.current_model
    if not m.api_key:
        print(f"  ⚠️  模型 '{name}' 未设置 API Key！请在环境变量中配置。")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mini Agent — 从零搭建的 AI Agent")
    parser.add_argument("query", nargs="*", help="单次查询的文本（可选）")
    parser.add_argument("--model", "-m", help="指定使用的模型（默认从配置读取）")
    args = parser.parse_args()

    if args.query:
        # 单次查询模式
        single_query(" ".join(args.query), model_name=args.model)
    else:
        # 交互模式
        interactive_mode(initial_model=args.model)

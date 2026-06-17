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
from core import keyring


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
        if user_input == "/key":
            _show_key_menu()
            continue
        if user_input.startswith("/key "):
            _handle_key_command(user_input[5:].strip())
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
    /key         — 管理加密 Key 存储
    /key set     — 加密保存 API Key（例如 /key set deepseek sk-xxx）
    /key remove  — 删除指定模型的 Key
    /key clear   — 清除所有 Key
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
        print(f"  ⚠️  模型 '{name}' 未设置 API Key！使用 /key set {name} sk-xxx 加密保存。")
    print()


def _show_key_menu():
    """显示 Key 管理界面"""
    kr_keys = keyring.list_keys()
    print("\n🔑 加密 Key 存储:")
    print(f"  存储位置: {keyring.KEYRING_FILE}")
    if kr_keys:
        print(f"  已保存 ({len(kr_keys)}):")
        for name in kr_keys:
            print(f"    • {name}")
        print()
        print(f"  命令:")
        print(f"    /key set <模型名> <API Key>   — 加密保存 Key")
        print(f"    /key remove <模型名>          — 删除指定 Key")
        print(f"    /key clear                    — 清除所有 Key")
    else:
        print(f"  📭 尚未保存任何 Key")
        print()
        print(f"  用法: /key set deepseek sk-xxxxxxxxxxxxxxxx")
        print(f"  Key 会被加密存储在 ~/.mini-agent/keys.enc，仅当前机器可解密。")
    print()


def _handle_key_command(args: str):
    """处理 /key 子命令"""
    import json

    parts = args.split(maxsplit=1)
    if not parts:
        _show_key_menu()
        return

    cmd = parts[0]

    if cmd == "set" and len(parts) < 2:
        print("❌ 用法: /key set <模型名> <API Key>")
        print("   例如: /key set deepseek sk-xxxxxxxxxxxxxxxx")
        return

    if cmd == "set":
        rest = parts[1].strip()
        # 解析第一个空格分割：模型名 和 Key
        space_idx = rest.find(" ")
        if space_idx < 0:
            print("❌ 用法: /key set <模型名> <API Key>")
            print("   例如: /key set deepseek sk-xxxxxxxxxxxxxxxx")
            return
        model_name = rest[:space_idx].strip()
        api_key = rest[space_idx + 1:].strip()

        if not model_name or not api_key:
            print("❌ 模型名和 Key 不能为空")
            return

        # 检查模型名是否在配置中
        cfg = get_config()
        if model_name not in cfg.models:
            print(f"⚠️  模型 '{model_name}' 不在配置中，将存档但无法直接使用。")
            yn = input(f"   确认保存? (y/N): ").strip().lower()
            if yn != "y":
                print("   已取消")
                return

        keyring.save_key(model_name, api_key)
        # 重新加载配置，让新 Key 生效
        reload_config()
        print(f"✅ Key 已加密保存！模型 '{model_name}' 现在可用。")
        print(f"   加密文件: {keyring.KEYRING_FILE} (仅当前机器可解密)")
        return

    if cmd == "remove" and len(parts) < 2:
        print("❌ 用法: /key remove <模型名>")
        print("   例如: /key remove deepseek")
        return

    if cmd == "remove":
        model_name = parts[1].strip()
        if keyring.delete_key(model_name):
            reload_config()
            print(f"✅ 已删除 '{model_name}' 的 Key")
        else:
            print(f"❌ 模型 '{model_name}' 没有保存的 Key")
        return

    if cmd == "clear":
        kr_keys = keyring.list_keys()
        if not kr_keys:
            print("📭 Key 存储已经为空")
            return
        print(f"⚠️  将删除所有 Key ({', '.join(kr_keys)})")
        yn = input("   确认? (y/N): ").strip().lower()
        if yn != "y":
            print("   已取消")
            return
        keyring.clear_keys()
        reload_config()
        print("✅ 所有 Key 已清除")
        return

    if cmd == "list":
        _show_key_menu()
        return

    # 未知子命令
    print(f"❌ 未知命令: /key {cmd}")
    print("   可用: set, remove, clear, list")
    print("   例如: /key set deepseek sk-xxxxxxxxxxxxxxxx")


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

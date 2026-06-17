"""
工具执行引擎 — Tool Calling Loop

这是 Agent 最核心的执行逻辑，它的工作流程：

  1.  LLM 返回包含 tool_calls 的响应
  2.  遍历每个 tool_call，调用对应的工具函数
  3.  把工具结果作为 tool-role 消息追加回上下文
  4.  再次调用 LLM，让它根据工具结果生成回复
  5.  如果 LLM 又返回 tool_calls，重复步骤 1-4
  6.  当 LLM 返回纯文本回复时，结束循环

最大递归层数（MAX_TURNS）防止无限循环。
"""
import json
from core import llm
from core.context import Context
from tools import registry

# 单次回复的最大工具调用轮次
MAX_TURNS = 5


def run_agent(context: Context, user_input: str) -> str:
    """
    执行一次完整的 Agent 回复流程
    ──────────────────────────────
    参数:
      context    — 对话上下文（维护消息历史）
      user_input — 用户输入
    
    返回:
      最终的文本回复
    """
    # 1. 添加用户输入到上下文
    context.add_user(user_input)

    # 2. 获取可用工具的定义
    tools = registry.get_schemas()
    
    # 3. 工具调用环
    turn = 0
    while turn < MAX_TURNS:
        turn += 1

        # 3a. 调用 LLM
        response = llm.chat(
            messages=context.get_messages(),
            tools=tools,
        )

        # 3b. 如果 LLM 没有要求调用工具，说明要输出最终回复了
        if not response.tool_calls:
            context.add_assistant(content=response.content)
            return response.content

        # 3c. LLM 要求调用工具 →
        #     先把包含 tool_calls 的 assistant 消息记入上下文
        context.add_assistant(
            tool_calls=response.tool_calls,
            content=response.content,
        )

        # 3d. 逐个执行工具
        for tc in response.tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            
            print(f"\n  🔧 调用工具: {name}({json.dumps(args, ensure_ascii=False)})")
            result = registry.execute(name, args)
            print(f"  ✅ 结果: {result[:200]}")

            # 把工具结果加回上下文
            context.add_tool_result(
                tool_call_id=tc["id"],
                name=name,
                result=result,
            )

    # 超过最大轮次，给出退路
    fallback = "我已尝试多次调用工具但未完成。请重新描述你的需求。"
    context.add_assistant(content=fallback)
    return fallback

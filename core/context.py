"""
对话上下文管理 — 维护消息历史
"""
from dataclasses import dataclass, field

SYSTEM_PROMPT = """你是一个智能助手，可以通过调用工具来帮助用户。
你的能力：
- 计算数学表达式
- 读取文件内容
- 搜索网络信息

请根据用户的请求，自主决定是否调用工具。调用工具后，根据工具返回的结果组织最终回复。
"""


@dataclass
class Context:
    """
    对话上下文，维护 messages 列表
    完整的多轮对话消息序列（参考 API 文档）：
    
    1. system — 系统提示词
    2. user   — 用户本轮输入
    3. assistant — 模型的工具调用（含 tool_calls）
    4. tool   — 工具执行结果（对应上一步的 tool_call_id）
    5. assistant — 模型根据工具结果生成的最终回复
    6. user   — 用户下一轮输入...
    """
    messages: list[dict] = field(default_factory=list)
    max_tokens: int = 128_000  # 上下文窗口上限（保守值）

    def __post_init__(self):
        # 初始化时加入 system prompt
        self.messages.append({
            "role": "system",
            "content": SYSTEM_PROMPT,
        })

    def add_user(self, text: str):
        """添加用户输入"""
        self.messages.append({"role": "user", "content": text})
        self._trim_if_needed()

    def add_assistant(self, content: str = "", tool_calls: list | None = None):
        """添加助手回复（可能是文本、工具调用、或两者）"""
        msg = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": _json_str(tc["arguments"]),
                    },
                }
                for tc in tool_calls
            ]
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, name: str, result: str):
        """添加工具执行结果
        
        关键协议细节：
        - role 必须是 "tool"
        - tool_call_id 必须匹配 assistant 消息中的 tool_calls[].id
        - content 是工具返回的字符串结果
        """
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": str(result)[:4000],  # 防止结果过大撑爆上下文
        })

    def _trim_if_needed(self):
        """简单的上下文裁剪：超过阈值时丢掉最早的 user/assistant 对"""
        # 估算 token 数（粗略: 1 char ≈ 0.25 token）
        total = sum(len(str(m)) for m in self.messages)
        while total > self.max_tokens and len(self.messages) > 3:
            # 保留 system + 最近的对话，丢掉最旧的 user/assistant 对
            self.messages.pop(1)  # 移除 system 后的第一条
            if len(self.messages) > 2:
                self.messages.pop(1)  # 再移除下一条（如果它是配对）
            total = sum(len(str(m)) for m in self.messages)

    def get_messages(self) -> list[dict]:
        """获取当前对话消息列表"""
        return self.messages

    def reset(self):
        """重置对话（保留 system prompt）"""
        self.messages = [self.messages[0]]


def _json_str(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)

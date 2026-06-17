"""
计...器工具 — 安全计算数学表达式
"""
import ast
import operator

from .registry import register

# 白名单：只允许这些操作
ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> float:
    """
    安全的表达式求值（不用 eval）
    只允许数字、运算符、括号、变量 x
    """
    # 清理：移除空白
    expr = expr.strip()
    if not expr:
        raise ValueError("表达式不能为空")

    # 解析为 AST
    tree = ast.parse(expr, mode="eval")
    
    def _eval(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"不支持的字面量: {type(node.value).__name__}")
        elif isinstance(node, ast.BinOp):
            op = ALLOWED_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"不支持的操作符: {type(node.op).__name__}")
            return op(_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            op = ALLOWED_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"不支持的操作符: {type(node.op).__name__}")
            return op(_eval(node.operand))
        raise ValueError(f"不支持的语法: {type(node).__name__}")
    
    return _eval(tree.body)


@register(
    name="calculator",
    description="计算数学表达式，支持 + - * / ** // % 和括号",
    parameters={
        "type": "object",
        "properties": {
            "expr": {
                "type": "string",
                "description": "要计算的数学表达式，例如 '1+2*3' 或 '2**10'",
            }
        },
        "required": ["expr"],
    },
)
def calculator(args: dict) -> str:
    expr = args["expr"]
    result = _safe_eval(expr)
    # 处理浮点数精度
    if isinstance(result, float):
        result = round(result, 10)
    return f"{expr} = {result}"

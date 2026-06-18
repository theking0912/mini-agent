"""导入测试 — CI 用"""
import sys

sys.path.insert(0, ".")

from tools import registry

tools = list(registry._registry.keys())
print(f"tools registered: {tools}")
assert len(tools) == 3, f"Expected 3 tools, got {len(tools)}"
print("All imports OK")

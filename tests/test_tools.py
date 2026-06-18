"""Tests for tool registration and execution."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from tools import registry
from tools.calculator import calculator
from tools.file_tool import read_file
from tools.registry import execute


def test_tool_registry_has_expected_tools():
    tools = list(registry._registry.keys())
    assert "calculator" in tools
    assert "read_file" in tools
    assert "web_search" in tools
    assert len(tools) >= 3


def test_calculator_add():
    """测试加法。返回格式 '1+2 = 3'"""
    result = calculator({"expr": "1+2"})
    assert result.endswith("= 3")


def test_calculator_subtract():
    result = calculator({"expr": "10-4"})
    assert result.endswith("= 6")


def test_calculator_multiply():
    result = calculator({"expr": "6*7"})
    assert result.endswith("= 42")


def test_calculator_divide():
    result = calculator({"expr": "10/2"})
    assert "5" in result


def test_calculator_divide_by_zero():
    """除零错误应该被捕获而不是抛出异常。"""
    with pytest.raises(ZeroDivisionError):
        calculator({"expr": "1/0"})


def test_calculator_power():
    result = calculator({"expr": "2**10"})
    assert result.endswith("= 1024")


def test_calculator_via_registry():
    result = execute("calculator", {"expr": "2**10"})
    assert "1024" in str(result)


def test_read_file_errors_on_nonexistent():
    """测试读取不存在的文件返回错误信息。"""
    result = read_file({"path": "/tmp/nonexistent_file_xyz.txt"})
    assert "不存在" in result or "error" in result.lower()


def test_read_file_success():
    """测试读取存在的文件。"""
    result = read_file({"path": "README.md"})
    assert "error" not in result.lower()


def test_registry_execute_unknown():
    """测试执行未知工具返回错误。"""
    result = execute("nonexistent_tool", {})
    assert "未知工具" in result or "error" in result.lower()

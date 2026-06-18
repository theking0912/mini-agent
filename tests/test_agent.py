"""Tests for the core agent loop."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_agent_imports():
    """Verify all agent modules can be imported without errors."""
    import agent
    assert hasattr(agent, '__name__')


def test_tool_runner_imports():
    """Verify tool runner imports and descriptions."""
    from core.tool_runner import run_agent
    from core.context import Context
    assert run_agent is not None


def test_context_is_working():
    """Verify context manager works."""
    from core.context import Context
    ctx = Context()
    ctx.add_user("Hello")
    assert len(ctx.messages) >= 2  # system + user

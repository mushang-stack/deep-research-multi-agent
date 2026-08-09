import pytest

from core.tool_registry import ToolRegistry, ToolNotFoundError


def _echo(text):
    return {"echo": text}


def test_register_and_schemas():
    reg = ToolRegistry()
    reg.register("echo", _echo, description="回显",
                 parameters={"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]})
    schemas = reg.schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "echo"
    assert "text" in schemas[0]["function"]["parameters"]["properties"]


def test_execute_passes_kwargs():
    reg = ToolRegistry()
    reg.register("echo", _echo, description="d", parameters={"type": "object"})
    assert reg.execute("echo", {"text": "hi"}) == {"echo": "hi"}


def test_execute_unknown_raises():
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.execute("nope", {})


def test_names():
    reg = ToolRegistry()
    reg.register("a", lambda: 1, description="d", parameters={"type": "object"})
    reg.register("b", lambda: 2, description="d", parameters={"type": "object"})
    assert set(reg.names()) == {"a", "b"}

from src.handler import handler


def test_handler_returns_ok():
    assert handler({"a": 1}, None) == {"ok": True}

import json
import logging

import pytest

from ironbridge.jcl.stub.handler import Abend, handler


class Ctx:
    aws_request_id = "req-1"


def test_default_rc_zero(monkeypatch):
    monkeypatch.delenv("ABEND_STEPS", raising=False)
    assert handler({"job": "J", "step": "S1", "pgm": "IDCAMS"}, Ctx())["rc"] == 0


def test_simulated_rc(monkeypatch):
    monkeypatch.delenv("ABEND_STEPS", raising=False)
    out = handler({"job": "J", "step": "S1", "simulate": {"S1": 8, "S2": 4}}, Ctx())
    assert out == {"job": "J", "step": "S1", "rc": 8}


def test_abend_listed_step(monkeypatch):
    monkeypatch.setenv("ABEND_STEPS", "X.Y, J.S1")
    with pytest.raises(Abend):
        handler({"job": "J", "step": "S1"}, Ctx())
    assert handler({"job": "J", "step": "S2"}, Ctx())["rc"] == 0


def test_logs_are_json(monkeypatch, caplog):
    monkeypatch.delenv("ABEND_STEPS", raising=False)
    with caplog.at_level(logging.INFO):
        handler({"job": "J", "step": "S1", "pgm": "SORT"}, Ctx())
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["event"] == "step" and rec["pgm"] == "SORT" and rec["request_id"] == "req-1"

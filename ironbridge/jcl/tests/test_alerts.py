import json
import logging
from pathlib import Path

import pytest

from ironbridge.jcl.alerts import handler as h

FIX = Path(__file__).parent / "fixtures" / "alerts"
ACCT = "111122223333"
SM = f"arn:aws:states:us-east-1:{ACCT}:stateMachine:ironbridge-m4-TRANBKP"
EXEC = f"arn:aws:states:us-east-1:{ACCT}:execution:ironbridge-m4-TRANBKP:run-1"
TAGS = {
    "ironbridge:job": "TRANBKP",
    "ironbridge:owner": "carddemo-batch-oncall",
    "ironbridge:owner-source": "owners-file",
    "ironbridge:runbook": "https://example.com/runbook",
}


def history(name: str) -> list[dict]:
    return json.loads((FIX / f"{name}.json").read_text())["events"]


def detail(status="FAILED", redrive_count=0, **kw) -> dict:
    d = {
        "executionArn": EXEC,
        "stateMachineArn": SM,
        "name": "run-1",
        "status": status,
        "redriveCount": redrive_count,
        "redriveStatus": "REDRIVABLE",
        "redriveStatusReason": f"Execution is {status} and is redrivable",
        "error": None,
        "cause": None,
    }
    d.update(kw)
    return d


def test_abend_finds_step_error_and_cause():
    f = h.find_failure(history("tranbkp-abend"))
    assert f["step"] == "STEP05" and f["event"] == "TaskFailed" and f["error"] == "Abend"
    assert "S0C7 simulated in TRANBKP.STEP05" in f["cause"]


def test_failed_again_after_redrive_names_the_same_step():
    # No new TaskStateEntered after ExecutionRedriven. The step comes from previousEventId.
    f = h.find_failure(history("tranbkp-redrive-failed-again"))
    assert f["step"] == "STEP05" and f["error"] == "Abend"


def test_reverse_order_history_gives_the_same_answer():
    events = list(reversed(history("tranbkp-redrive-failed-again")))
    assert h.find_failure(events)["step"] == "STEP05"


def test_aborted_and_timed_out_name_the_running_step():
    a = h.find_failure(history("tranbkp-aborted"))
    assert a["step"] == "STEP05" and a["event"] == "TaskStateAborted"
    t = h.find_failure(history("tranbkp-timed-out"))
    assert t["step"] == "STEP05" and t["error"] == "States.Timeout"


def test_parent_names_the_child_job_and_its_run():
    f = h.find_failure(history("daily-parent-child-failed"))
    assert f["step"] == "TRANBKP" and f["error"] == "States.TaskFailed"
    why = h.explain_cause(f["cause"])
    assert why["child"].endswith("ironbridge-m4-TRANBKP:0a1b2c3d-0000-4000-8000-000000000002")
    assert why["abend"] == "S0C7" and why["message"].startswith("Abend S0C7 simulated")


def test_empty_history_is_unknown_not_a_crash():
    assert h.find_failure([])["step"] == "unknown"


@pytest.mark.parametrize(
    "text,code",
    [
        ("S0C7 simulated", "S0C7"),
        ("ABEND SB37 on DD", "SB37"),
        ("U4038 here", "U4038"),
        ("SEED the table", ""),
        ("plain error", ""),
    ],
)
def test_abend_codes(text, code):
    assert h.explain_cause(text)["abend"] == code


def test_message_has_job_step_owner_runbook_link_and_redrive():
    f = h.find_failure(history("tranbkp-abend"))
    a = h.format_alert(detail(), f, TAGS, "us-east-1")
    assert a["subject"] == "ironbridge FAILED: TRANBKP at STEP05"
    m = a["message"]
    for want in (
        "Job:            TRANBKP",
        "Failed step:    STEP05",
        "Return code:    none, the step abended with S0C7",
        "Error:          Abend",
        "Cause:          S0C7 simulated in TRANBKP.STEP05",
        "Owner:          carddemo-batch-oncall (from owners.json)",
        "Runbook:        https://example.com/runbook",
        "Redrive count:  0",
        f"aws stepfunctions redrive-execution --execution-arn {EXEC}",
        f"https://us-east-1.console.aws.amazon.com/states/home?region=us-east-1"
        f"#/v2/executions/details/{EXEC}",
    ):
        assert want in m, want
    assert "stackTrace" not in m
    attrs = {k: v["StringValue"] for k, v in a["attributes"].items()}
    assert attrs == {
        "job": "TRANBKP",
        "step": "STEP05",
        "owner": "carddemo-batch-oncall",
        "status": "FAILED",
    }


def test_no_tags_falls_back_and_says_why():
    f = h.find_failure(history("tranbkp-abend"))
    a = h.format_alert(detail(), f, {}, "us-east-1")
    assert "Job:            ironbridge-m4-TRANBKP" in a["message"]
    assert "Owner:          unknown" in a["message"]
    assert "Runbook:        none set" in a["message"]
    tags = dict(TAGS, **{"ironbridge:owner": "unassigned", "ironbridge:owner-source": "none"})
    a = h.format_alert(detail(), f, tags, "us-east-1")
    assert "Owner:          unassigned (not set. NOTIFY= has no fixed user" in a["message"]


def test_not_redrivable_is_said():
    f = h.find_failure(history("tranbkp-aborted"))
    d = detail(
        "ABORTED",
        redriveStatus="NOT_REDRIVABLE",
        redriveStatusReason="Execution is ABORTED and cannot be redriven",
    )
    a = h.format_alert(d, f, TAGS, "us-east-1")
    assert a["subject"] == "ironbridge ABORTED: TRANBKP at STEP05"
    assert "Return code:    none, the execution was aborted" in a["message"]
    assert "The event says NOT_REDRIVABLE" in a["message"]


def test_subject_is_short_ascii():
    f = {"step": "S" * 200, "event": "", "error": "", "cause": ""}
    s = h.format_alert(detail(), f, TAGS, "us-east-1")["subject"]
    assert len(s) < 100 and s.isascii()


class FakeSfn:
    def __init__(self, events, fail_tags=False):
        self.events, self.fail_tags = events, fail_tags

    def get_paginator(self, name):
        assert name == "get_execution_history"
        events = self.events

        class P:
            def paginate(self, executionArn, PaginationConfig):  # noqa: N803 - boto3 names
                assert executionArn == EXEC
                yield {"events": events[:5]}
                yield {"events": events[5:]}

        return P()

    def list_tags_for_resource(self, resourceArn):  # noqa: N803
        if self.fail_tags:
            raise RuntimeError("AccessDenied")
        assert resourceArn == SM
        return {"tags": [{"key": k, "value": v} for k, v in TAGS.items()]}


class FakeSns:
    def __init__(self):
        self.sent = []

    def publish(self, **kw):
        self.sent.append(kw)
        return {"MessageId": f"m-{len(self.sent)}"}


class Ctx:
    aws_request_id = "req-1"


@pytest.fixture
def fakes(monkeypatch):
    sns = FakeSns()
    monkeypatch.setenv("TOPIC_ARN", f"arn:aws:sns:us-east-1:{ACCT}:ironbridge-m4-job-alerts")
    monkeypatch.setattr(h, "_clients", {"sns": sns})
    return sns


def test_handler_publishes_one_alert(fakes, caplog):
    h._clients["stepfunctions"] = FakeSfn(history("tranbkp-redrive-failed-again"))
    event = {"region": "us-east-1", "detail": detail(redrive_count=1)}
    with caplog.at_level(logging.INFO):
        out = h.handler(event, Ctx())
    assert out["messageId"] == "m-1" and len(fakes.sent) == 1
    sent = fakes.sent[0]
    assert sent["TopicArn"].endswith(":ironbridge-m4-job-alerts")
    assert "Failed step:    STEP05" in sent["Message"]
    assert "Redrive count:  1" in sent["Message"]
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["event"] == "alert_sent" and rec["redrive_count"] == 1 and rec["step"] == "STEP05"


def test_handler_still_alerts_when_reads_fail(fakes):
    class Broken:
        def get_paginator(self, name):
            raise RuntimeError("AccessDenied")

        def list_tags_for_resource(self, resourceArn):  # noqa: N803
            raise RuntimeError("AccessDenied")

    h._clients["stepfunctions"] = Broken()
    d = detail(error="Abend", cause=json.dumps({"errorMessage": "S0C7 simulated"}))
    h.handler({"region": "us-east-1", "detail": d}, Ctx())
    m = fakes.sent[0]["Message"]
    assert "Failed step:    unknown" in m and "Owner:          unknown" in m
    assert "Error:          Abend" in m and "abended with S0C7" in m


def test_handler_raises_when_publish_fails(fakes):
    class NoSns:
        def publish(self, **kw):
            raise RuntimeError("KMS.AccessDenied")

    h._clients["stepfunctions"] = FakeSfn(history("tranbkp-abend"))
    h._clients["sns"] = NoSns()
    with pytest.raises(RuntimeError):
        h.handler({"region": "us-east-1", "detail": detail()}, Ctx())

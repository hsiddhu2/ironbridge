"""Offline run of scripts/alert_test.py against fake AWS clients. The fake event bus sends a
message only on FAILED, like the real rule, and the message text comes from the real alert
formatter. This checks the script's flow and cleanup, not AWS."""

import csv
import json
import sys
from pathlib import Path

import pytest

from ironbridge.jcl.alerts import handler as h

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import alert_test  # noqa: E402

ACCT = "111122223333"
TOPIC = f"arn:aws:sns:us-east-1:{ACCT}:ironbridge-m4-job-alerts"
SM = f"arn:aws:states:us-east-1:{ACCT}:stateMachine:ironbridge-m4-TRANBKP"


class World:
    def __init__(self, duplicate_on_success=False):
        self.env = {"ABEND_STEPS": "", "LOG_LEVEL": "INFO"}
        self.queue, self.queues, self.subs = [], set(), set()
        self.status, self.redrives, self.clock = "", 0, 0.0
        self.duplicate_on_success = duplicate_on_success
        self.arn = f"arn:aws:states:us-east-1:{ACCT}:execution:ironbridge-m4-TRANBKP:run-1"

    def run(self):
        failed = "TRANBKP.STEP05" in self.env["ABEND_STEPS"]
        self.status = "FAILED" if failed else "SUCCEEDED"
        if failed or self.duplicate_on_success:
            detail = {
                "executionArn": self.arn,
                "stateMachineArn": SM,
                "status": self.status,
                "redriveCount": self.redrives,
            }
            failure = {"step": "STEP05", "event": "TaskFailed", "error": "Abend", "cause": "S0C7"}
            tags = {"ironbridge:job": "TRANBKP", "ironbridge:owner": "carddemo-batch-oncall"}
            a = h.format_alert(detail, failure, tags, "us-east-1")
            attrs = {k: v for k, v in a["attributes"].items()}
            self.queue.append({"Body": a["message"], "MessageAttributes": attrs})


class Sfn:
    def __init__(self, w):
        self.w = w

    def start_execution(self, stateMachineArn, input):  # noqa: A002, N803
        self.w.run()
        return {"executionArn": self.w.arn}

    def redrive_execution(self, executionArn):  # noqa: N803
        self.w.redrives += 1
        self.w.run()

    def describe_execution(self, executionArn):  # noqa: N803
        return {"status": self.w.status}


class Lam:
    def __init__(self, w):
        self.w = w

    def get_function_configuration(self, FunctionName):  # noqa: N803
        return {"Environment": {"Variables": dict(self.w.env)}}

    def update_function_configuration(self, FunctionName, Environment):  # noqa: N803
        self.w.env = dict(Environment["Variables"])

    def get_waiter(self, name):
        class W:
            def wait(self, **kw):
                pass

        return W()


class Sns:
    def __init__(self, w):
        self.w = w

    def subscribe(self, **kw):
        assert kw["Attributes"] == {"RawMessageDelivery": "true"}
        self.w.subs.add("sub-1")
        return {"SubscriptionArn": "sub-1"}

    def unsubscribe(self, SubscriptionArn):  # noqa: N803
        self.w.subs.discard(SubscriptionArn)


class Sqs:
    def __init__(self, w):
        self.w = w

    def create_queue(self, QueueName, Attributes, tags):  # noqa: N803
        assert Attributes["SqsManagedSseEnabled"] == "true"
        assert TOPIC in Attributes["Policy"]
        self.w.queues.add(QueueName)
        return {"QueueUrl": f"https://sqs.us-east-1.amazonaws.com/{ACCT}/{QueueName}"}

    def receive_message(self, QueueUrl, WaitTimeSeconds, **kw):  # noqa: N803
        self.w.clock += WaitTimeSeconds
        msgs = [dict(m, ReceiptHandle="r") for m in self.w.queue]
        self.w.queue = []
        return {"Messages": msgs}

    def delete_message(self, **kw):
        pass

    def delete_queue(self, QueueUrl):  # noqa: N803
        self.w.queues.discard(QueueUrl.rsplit("/", 1)[-1])

    def get_queue_attributes(self, **kw):
        return {"Attributes": {"ApproximateNumberOfMessages": "0"}}


class Session:
    def __init__(self, w):
        self.clients = {"stepfunctions": Sfn(w), "lambda": Lam(w), "sns": Sns(w), "sqs": Sqs(w)}

    def client(self, name, config=None):
        return self.clients[name]


def run(monkeypatch, tmp_path, w: World, s: Session | None = None) -> int:
    build = tmp_path / "build"
    build.mkdir()
    (build / "owners.json").write_text(json.dumps({"TRANBKP": {"owner": "carddemo-batch-oncall"}}))
    out = {
        "AlertTopicArn": TOPIC,
        "StubFunctionName": "ironbridge-m4-rc-stub",
        "JobTRANBKPArn": SM,
        "AlertDlqUrl": f"https://sqs.us-east-1.amazonaws.com/{ACCT}/ironbridge-m4-alerts-dlq",
    }
    s = s or Session(w)
    monkeypatch.setattr(alert_test, "ROOT", tmp_path)
    monkeypatch.setattr(alert_test, "REGION", "us-east-1")
    monkeypatch.setattr(alert_test, "outputs", lambda: out)
    monkeypatch.setattr(alert_test, "session", lambda: (s, None))
    monkeypatch.setattr(alert_test.time, "sleep", lambda sec: None)
    monkeypatch.setattr(alert_test.time, "monotonic", lambda: w.clock)
    return alert_test.main()


def results(tmp_path) -> dict[str, dict]:
    with (tmp_path / "results-alerts.csv").open() as f:
        return {r["check"]: r for r in csv.DictReader(f)}


def test_alert_test_passes_and_cleans_up(monkeypatch, tmp_path, capsys):
    w = World()
    assert run(monkeypatch, tmp_path, w) == 0
    text = capsys.readouterr().out
    assert "alert checks passed 11/11" in text
    assert "Failed step:    STEP05" in text
    assert not w.queues and not w.subs and w.env["ABEND_STEPS"] == ""
    rows = results(tmp_path)
    assert len(rows) == 11 and all(r["passed"] == "true" for r in rows.values())


def test_alert_on_successful_redrive_fails_the_check(monkeypatch, tmp_path):
    w = World(duplicate_on_success=True)
    assert run(monkeypatch, tmp_path, w) == 1
    bad = {k for k, r in results(tmp_path).items() if r["passed"] != "true"}
    assert bad == {"alerts after successful redrive"}
    assert not w.queues and not w.subs


def test_cleanup_runs_when_aws_fails(monkeypatch, tmp_path):
    w = World()
    s = Session(w)

    def boom(**kw):
        raise RuntimeError("throttled")

    s.clients["stepfunctions"].start_execution = boom
    with pytest.raises(RuntimeError):
        run(monkeypatch, tmp_path, w, s)
    assert not w.queues and not w.subs and w.env["ABEND_STEPS"] == ""

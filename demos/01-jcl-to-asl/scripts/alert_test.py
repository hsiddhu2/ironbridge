"""Deployed check for failure alerts. Run with `make alert-test` after `make deploy`.

1. Subscribe a temporary SQS queue to the alert topic.
2. Abend TRANBKP STEP05 and expect one alert naming the job, the step and the owner.
3. Redrive while the abend is still set. The run fails again and must alert again.
4. Clear the abend and redrive. The run succeeds and must send nothing.
5. The alert DLQ must be empty.

The queue, the subscription and the abend setting are removed even if a check fails.
Rows are appended to results-alerts.csv. Exit code 1 if any check fails.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import UTC, datetime

from awsctx import REGION, ROOT, STACK, guarded, outputs, session
from measure import set_abend, wait

JOB, STEP = "TRANBKP", "STEP05"
FIRST_WAIT = 180  # seconds to wait for an alert. Typical delivery is seconds.
GRACE = 20  # seconds to keep listening after an alert, to catch a duplicate
QUIET_WAIT = 60  # seconds of silence required after the successful redrive
RUN = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
COLUMNS = ["run_id", "utc_time", "check", "expected", "actual", "passed", "execution_arn", "region"]


def make_queue(sqs, topic_arn: str) -> tuple[str, str]:
    name = f"{STACK}-alert-test-{RUN}"
    account = topic_arn.split(":")[4]
    arn = f"arn:aws:sqs:{REGION}:{account}:{name}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "sns.amazonaws.com"},
                "Action": "sqs:SendMessage",
                "Resource": arn,
                "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
            },
            {
                "Effect": "Deny",
                "Principal": "*",
                "Action": "sqs:*",
                "Resource": arn,
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
        ],
    }
    url = sqs.create_queue(
        QueueName=name,
        Attributes={"SqsManagedSseEnabled": "true", "Policy": json.dumps(policy)},
        tags={"project": STACK},
    )["QueueUrl"]
    return url, arn


def collect(sqs, url: str, arn: str, seconds: int, until_one: bool) -> tuple[list, int]:
    """Messages about this execution, and how many others arrived. With until_one, stop
    GRACE seconds after the first match instead of waiting the full time."""
    mine, others = [], 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        r = sqs.receive_message(
            QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=5, MessageAttributeNames=["All"]
        )
        for m in r.get("Messages", []):
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
            attrs = {k: v["StringValue"] for k, v in m.get("MessageAttributes", {}).items()}
            if arn in m["Body"]:
                mine.append({"body": m["Body"], "attrs": attrs})
                if until_one and len(mine) == 1:
                    deadline = min(deadline, time.monotonic() + GRACE)
            else:
                others += 1
    return mine, others


def main() -> int:
    out = outputs()
    s, cfg = session()
    sfn, lam = s.client("stepfunctions", config=cfg), s.client("lambda", config=cfg)
    sns, sqs = s.client("sns", config=cfg), s.client("sqs", config=cfg)
    topic, fn, sm = out["AlertTopicArn"], out["StubFunctionName"], out[f"Job{JOB}Arn"]
    owner = json.loads((ROOT / "build" / "owners.json").read_text())[JOB]["owner"]
    rows: list[dict] = []
    arn = ""

    def check(name: str, expected, actual) -> None:
        ok = expected == actual
        rows.append(
            {
                "run_id": f"{RUN}-alerts-{len(rows) + 1:03d}",
                "utc_time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "check": name,
                "expected": expected,
                "actual": actual,
                "passed": str(ok).lower(),
                "execution_arn": arn,
                "region": REGION,
            }
        )

    def alert_fields(msgs: list) -> dict:
        a = msgs[0]["attrs"] if msgs else {}
        return {k: a.get(k, "") for k in ("job", "step", "owner", "status")}

    url, qarn = make_queue(sqs, topic)
    sub = None
    try:
        sub = sns.subscribe(
            TopicArn=topic,
            Protocol="sqs",
            Endpoint=qarn,
            Attributes={"RawMessageDelivery": "true"},
            ReturnSubscriptionArn=True,
        )["SubscriptionArn"]
        time.sleep(5)  # let the new subscription settle before the first failure
        want = {"job": JOB, "step": STEP, "owner": owner, "status": "FAILED"}

        set_abend(lam, fn, f"{JOB}.{STEP}")
        arn = sfn.start_execution(stateMachineArn=sm, input='{"simulate": {}}')["executionArn"]
        print("started", arn)
        check("run 1 status", "FAILED", wait(sfn, arn)["status"])
        msgs, others = collect(sqs, url, arn, FIRST_WAIT, until_one=True)
        check("alerts after failure", 1, len(msgs))
        check("alert fields after failure", want, alert_fields(msgs))
        check(
            "redrive count in alert 1", True, bool(msgs) and "Redrive count:  0" in msgs[0]["body"]
        )
        if msgs:
            print("\n--- alert 1 ---\n" + msgs[0]["body"])

        sfn.redrive_execution(executionArn=arn)  # abend still set: fails again
        time.sleep(1)
        check("redrive 1 status", "FAILED", wait(sfn, arn)["status"])
        msgs, n = collect(sqs, url, arn, FIRST_WAIT, until_one=True)
        others += n
        check("alerts after failed redrive", 1, len(msgs))
        check("alert fields after failed redrive", want, alert_fields(msgs))
        check(
            "redrive count in alert 2", True, bool(msgs) and "Redrive count:  1" in msgs[0]["body"]
        )

        set_abend(lam, fn, "")
        sfn.redrive_execution(executionArn=arn)
        time.sleep(1)
        check("redrive 2 status", "SUCCEEDED", wait(sfn, arn)["status"])
        msgs, n = collect(sqs, url, arn, QUIET_WAIT, until_one=False)
        others += n
        check("alerts after successful redrive", 0, len(msgs))

        dlq = sqs.get_queue_attributes(
            QueueUrl=out["AlertDlqUrl"], AttributeNames=["ApproximateNumberOfMessages"]
        )["Attributes"]["ApproximateNumberOfMessages"]
        check("alert DLQ depth", "0", dlq)
        if others:
            print(f"note: {others} alert(s) for other executions arrived and were ignored")
    finally:
        set_abend(lam, fn, "")
        if sub:
            sns.unsubscribe(SubscriptionArn=sub)
        sqs.delete_queue(QueueUrl=url)
        print(f"cleaned up: subscription and queue {url.rsplit('/', 1)[-1]}, abend cleared")

    path = ROOT / "results-alerts.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"\n{'check':<36}{'passed':<7}actual")
    for r in rows:
        print(f"{r['check']:<36}{r['passed']:<7}{r['actual']}")
    passed = sum(r["passed"] == "true" for r in rows)
    print(f"\nalert checks passed {passed}/{len(rows)}. Appended to {path.name}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(guarded(main))

"""Failure alert for converted jobs. EventBridge calls this on a FAILED, TIMED_OUT or ABORTED
Step Functions execution. It publishes one SNS message with the job, the failed step, the
error, the owner and runbook from the state machine tags, a console link and a redrive hint.

The EventBridge event has the execution's error and cause but not the failed state's name:
https://docs.aws.amazon.com/step-functions/latest/dg/eventbridge-integration.html
So the step comes from GetExecutionHistory. A history or tag read that fails still sends the
alert, with "unknown" in that field. Only a failed SNS publish raises, so Lambda retries it.

This file runs on its own in Lambda. It imports only the standard library and boto3.
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TAG_PREFIX = "ironbridge:"
# A z/OS abend code: S plus three hex digits is a system abend (S0C7, SB37), U plus four
# digits is a user abend (U4038). At least one digit, so a word like SEED does not match.
ABEND = re.compile(r"\b(S(?=[0-9A-F]{0,2}\d)[0-9A-F]{3}|U\d{4})\b")
# A Parallel or Map fails because something inside it failed. Prefer the inner event.
CONTAINER_FAILURES = {
    f"{kind}{end}"
    for kind in ("ParallelState", "MapState", "MapIteration", "MapRun")
    for end in ("Failed", "Aborted")
}
STEP_FAILURE_SUFFIXES = ("Failed", "TimedOut", "Aborted")
CAUSE_LIMIT = 600
SUBJECT_LIMIT = 100  # SNS Subject must be under 100 ASCII characters

_clients: dict = {}


def client(name: str):
    if name not in _clients:
        import boto3
        from botocore.config import Config

        cfg = Config(retries={"mode": "standard", "max_attempts": 5}, connect_timeout=3)
        _clients[name] = boto3.client(name, config=cfg)
    return _clients[name]


def _log(**fields) -> None:
    logger.info(json.dumps(fields, sort_keys=True, default=str))


def _details(e: dict) -> dict:
    return next((v for k, v in e.items() if k.endswith("EventDetails")), {}) or {}


def _entered(by_id: dict[int, dict], e: dict | None) -> dict | None:
    """Walk previousEventId back to the *StateEntered event that owns this event."""
    seen = 0
    while e is not None and not e["type"].endswith("StateEntered") and seen < 10000:
        e = by_id.get(e.get("previousEventId"))
        seen += 1
    return e if e is not None and e["type"].endswith("StateEntered") else None


def _is_step_failure(e: dict) -> bool:
    t = e["type"]
    return not t.startswith("Execution") and t.endswith(STEP_FAILURE_SUFFIXES)


def find_failure(events: list[dict]) -> dict:
    """Failed step, its error and cause, read from an execution history in any order.

    Only the latest attempt counts: events after the last ExecutionRedriven, or all of them
    if the execution was never redriven. A redriven Task has no new TaskStateEntered, so the
    step name comes from walking previousEventId back to the state that owns the failure.
    With no failure event (ABORTED, or TIMED_OUT at the execution level), the step is the
    state that was entered and never exited.
    """
    events = sorted(events, key=lambda e: e["id"])
    by_id = {e["id"]: e for e in events}
    start = max((e["id"] for e in events if e["type"] == "ExecutionRedriven"), default=0)
    latest = [e for e in events if e["id"] > start]
    end = next((e for e in reversed(latest) if e["type"].startswith("Execution")), None)
    end_details = _details(end) if end else {}

    fails = [e for e in latest if _is_step_failure(e)]
    pick = next((e for e in reversed(fails) if e["type"] not in CONTAINER_FAILURES), None)
    pick = pick or (fails[-1] if fails else None)
    state = _entered(by_id, pick) if pick else None
    if state is None:
        open_states: dict[str, dict] = {}
        for e in events:
            if e["type"].endswith("StateEntered"):
                open_states[e["stateEnteredEventDetails"]["name"]] = e
            elif e["type"].endswith("StateExited"):
                open_states.pop(e["stateExitedEventDetails"]["name"], None)
        state = max(open_states.values(), key=lambda e: e["id"], default=None)
    d = _details(pick) if pick else end_details
    return {
        "step": state["stateEnteredEventDetails"]["name"] if state else "unknown",
        "event": pick["type"] if pick else (end["type"] if end else "unknown"),
        "error": d.get("error") or end_details.get("error") or "",
        "cause": d.get("cause") or end_details.get("cause") or "",
    }


def read_history(execution_arn: str) -> list[dict]:
    events: list[dict] = []
    pages = client("stepfunctions").get_paginator("get_execution_history")
    for page in pages.paginate(executionArn=execution_arn, PaginationConfig={"PageSize": 1000}):
        events += page["events"]
    return events


def read_tags(state_machine_arn: str) -> dict[str, str]:
    tags = client("stepfunctions").list_tags_for_resource(resourceArn=state_machine_arn)["tags"]
    return {t["key"]: t["value"] for t in tags}


def explain_cause(cause: str) -> dict:
    """Pull the readable parts out of a cause. A Lambda error cause is JSON with errorMessage.
    A nested job's cause is the child's DescribeExecution output, with its own Error and Cause."""
    try:
        data = json.loads(cause)
    except (TypeError, ValueError):
        data = None
    out = {"message": cause, "child": ""}
    if isinstance(data, dict):
        if "errorMessage" in data:
            out["message"] = str(data["errorMessage"])
        elif "ExecutionArn" in data:
            out["child"] = str(data["ExecutionArn"])
            inner = explain_cause(str(data.get("Cause") or ""))
            out["message"] = " ".join(
                x for x in (str(data.get("Error") or ""), inner["message"]) if x
            )
    found = ABEND.search(out["message"])
    out["abend"] = found.group(1) if found else ""
    if len(out["message"]) > CAUSE_LIMIT:
        out["message"] = out["message"][:CAUSE_LIMIT] + " ..."
    return out


def console_url(region: str, execution_arn: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/states/home?region={region}"
        f"#/v2/executions/details/{execution_arn}"
    )


def format_alert(detail: dict, failure: dict, tags: dict[str, str], region: str) -> dict:
    """Subject, plain-text message and SNS message attributes for one failed execution."""
    sm_name = detail["stateMachineArn"].rsplit(":", 1)[-1]
    job = tags.get(f"{TAG_PREFIX}job") or sm_name
    owner = tags.get(f"{TAG_PREFIX}owner") or "unknown"
    source = tags.get(f"{TAG_PREFIX}owner-source", "")
    runbook = tags.get(f"{TAG_PREFIX}runbook") or "none set"
    status = detail.get("status", "FAILED")
    error = failure["error"] or detail.get("error") or ""
    why = explain_cause(failure["cause"] or detail.get("cause") or "")
    if why["abend"]:
        rc = f"none, the step abended with {why['abend']}"
    elif status == "FAILED":
        rc = "none, the step raised an error instead of ending with a return code"
    else:
        rc = f"none, the execution was {status.lower().replace('_', ' ')}"
    owner_note = {
        "owners-file": "from owners.json",
        "owners-file-default": "default from owners.json",
        "notify": "from NOTIFY= on the JOB card",
        "none": "NOTIFY=&SYSUID names whoever submits the job, so set an owner in owners.json",
    }.get(source, "")
    arn = detail["executionArn"]
    redrive = detail.get("redriveStatus") or ""
    lines = [
        f"ironbridge job alert: {job} {status}",
        "",
        f"Job:            {job}",
        f"Failed step:    {failure['step']}",
        f"Return code:    {rc}",
        f"Error:          {error or 'none given'}",
        f"Cause:          {why['message'] or 'none given'}",
    ]
    if why["child"]:
        lines.append(f"Child job run:  {why['child']}")
    lines += [
        f"Owner:          {owner}" + (f" ({owner_note})" if owner_note else ""),
        f"Runbook:        {runbook}",
        f"Execution:      {arn}",
        f"Redrive count:  {detail.get('redriveCount', 0)}",
        f"Console:        {console_url(region, arn)}",
        "",
        "Redrive: fix the cause first, then run",
        f"  aws stepfunctions redrive-execution --execution-arn {arn}",
        "Steps that already succeeded do not run again. The failed step runs again.",
    ]
    if redrive and redrive != "REDRIVABLE":
        reason = detail.get("redriveStatusReason") or ""
        lines.append(f"The event says {redrive}: {reason}. If redrive is refused, start a new run.")
    subject = f"ironbridge {status}: {job} at {failure['step']}"
    subject = subject.encode("ascii", "replace").decode()[: SUBJECT_LIMIT - 1]
    attrs = {"job": job, "step": failure["step"], "owner": owner, "status": status}
    return {
        "subject": subject,
        "message": "\n".join(lines) + "\n",
        "attributes": {k: {"DataType": "String", "StringValue": v} for k, v in attrs.items() if v},
    }


def handler(event: dict, context) -> dict:
    detail = event["detail"]
    arn = detail["executionArn"]
    region = event.get("region") or os.environ.get("AWS_REGION", "")
    try:
        failure = find_failure(read_history(arn))
    except Exception as exc:  # noqa: BLE001 - alert anyway, with the step unknown
        _log(event="history_read_failed", execution_arn=arn, error=repr(exc))
        failure = {"step": "unknown", "event": "", "error": "", "cause": ""}
    try:
        tags = read_tags(detail["stateMachineArn"])
    except Exception as exc:  # noqa: BLE001 - alert anyway, with the owner unknown
        _log(event="tags_read_failed", state_machine_arn=detail["stateMachineArn"], error=repr(exc))
        tags = {}
    alert = format_alert(detail, failure, tags, region)
    resp = client("sns").publish(
        TopicArn=os.environ["TOPIC_ARN"],
        Subject=alert["subject"],
        Message=alert["message"],
        MessageAttributes=alert["attributes"],
    )
    _log(
        event="alert_sent",
        execution_arn=arn,
        status=detail.get("status"),
        redrive_count=detail.get("redriveCount", 0),
        step=failure["step"],
        message_id=resp.get("MessageId"),
        request_id=getattr(context, "aws_request_id", ""),
    )
    return {"messageId": resp.get("MessageId"), "subject": alert["subject"]}

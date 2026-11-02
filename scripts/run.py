"""Smoke run: start the deployed Control-M folder parent once with every RC at 0.

Prints the parent status and the jobs it ran. The schedule stays DISABLED, so this is the
only way the parent runs.
"""

from __future__ import annotations

import json
import time

from awsctx import outputs, session


def main() -> int:
    out = outputs()
    s, cfg = session()
    sfn = s.client("stepfunctions", config=cfg)
    ex = sfn.start_execution(stateMachineArn=out["ParentArn"], input=json.dumps({"gdg": {}}))
    print("started", ex["executionArn"])
    while (d := sfn.describe_execution(executionArn=ex["executionArn"]))["status"] == "RUNNING":
        time.sleep(2)
    events = sfn.get_execution_history(executionArn=ex["executionArn"], maxResults=1000)["events"]
    ran = [e["stateEnteredEventDetails"]["name"] for e in events if e["type"] == "TaskStateEntered"]
    print("status", d["status"])
    print("jobs", " -> ".join(ran))
    if d["status"] == "SUCCEEDED":
        print("output", d["output"])
    return 0 if d["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""List anything left after `make destroy`: stack, state machines, functions, schedule,
log groups, roles, policies, SNS topics, SQS queues and EventBridge rules whose name starts
with the stack name. The SQS check also catches a queue that `make alert-test` failed to
delete."""

from __future__ import annotations

from awsctx import REGION, STACK, guarded, session
from botocore.exceptions import ClientError


def find(s, cfg) -> list[str]:
    found: list[str] = []
    try:
        st = s.client("cloudformation", config=cfg).describe_stacks(StackName=STACK)
        found.append(f"stack {STACK}: {st['Stacks'][0]['StackStatus']}")
    except ClientError:
        pass
    sfn = s.client("stepfunctions", config=cfg)
    for page in sfn.get_paginator("list_state_machines").paginate():
        found += [
            f"state machine {m['name']}"
            for m in page["stateMachines"]
            if m["name"].startswith(STACK)
        ]
    lam = s.client("lambda", config=cfg)
    for page in lam.get_paginator("list_functions").paginate():
        found += [
            f"function {f['FunctionName']}"
            for f in page["Functions"]
            if f["FunctionName"].startswith(STACK)
        ]
    sch = s.client("scheduler", config=cfg)
    for page in sch.get_paginator("list_schedules").paginate(NamePrefix=STACK):
        found += [f"schedule {x['Name']}" for x in page["Schedules"]]
    logs = s.client("logs", config=cfg)
    for prefix in (STACK, f"/aws/vendedlogs/states/{STACK}", f"/aws/lambda/{STACK}"):
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix):
            found += [f"log group {g['logGroupName']}" for g in page["logGroups"]]
    iam = s.client("iam", config=cfg)
    for page in iam.get_paginator("list_roles").paginate():
        found += [f"role {r['RoleName']}" for r in page["Roles"] if r["RoleName"].startswith(STACK)]
    for page in iam.get_paginator("list_policies").paginate(Scope="Local"):
        found += [
            f"policy {p['PolicyName']}"
            for p in page["Policies"]
            if p["PolicyName"].startswith(STACK)
        ]
    sns = s.client("sns", config=cfg)
    for page in sns.get_paginator("list_topics").paginate():
        found += [
            f"topic {t['TopicArn']}"
            for t in page["Topics"]
            if t["TopicArn"].rsplit(":", 1)[-1].startswith(STACK)
        ]
    sqs = s.client("sqs", config=cfg)
    for page in sqs.get_paginator("list_queues").paginate(QueueNamePrefix=STACK):
        found += [f"queue {u.rsplit('/', 1)[-1]}" for u in page.get("QueueUrls", [])]
    events = s.client("events", config=cfg)
    for page in events.get_paginator("list_rules").paginate(NamePrefix=STACK):
        found += [f"rule {r['Name']}" for r in page["Rules"]]
    return found


def main() -> int:
    s, cfg = session()
    found = find(s, cfg)
    for f in found:
        print(" -", f)
    print("LEFTOVERS FOUND" if found else f"clean: nothing named {STACK}* in {REGION}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(guarded(main))

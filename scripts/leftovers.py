"""List billable resources that may remain after `make destroy`.

Checks the stack, then the usual suspects that CDK retains by default.
"""

from __future__ import annotations

import argparse

import boto3
from botocore.exceptions import ClientError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()
    s = boto3.Session(region_name=args.region)
    found: list[str] = []

    cfn = s.client("cloudformation")
    try:
        st = cfn.describe_stacks(StackName=args.stack)["Stacks"][0]["StackStatus"]
        found.append(f"stack {args.stack} still exists: {st}")
    except ClientError:
        pass

    prefix = args.stack.lower()
    for b in s.client("s3").list_buckets().get("Buckets", []):
        if prefix in b["Name"].lower():
            found.append(f"s3 bucket {b['Name']}")

    logs = s.client("logs")
    for g in logs.describe_log_groups(logGroupNamePrefix="/aws/lambda/").get("logGroups", []):
        if prefix in g["logGroupName"].lower():
            found.append(f"log group {g['logGroupName']}")

    for t in s.client("dynamodb").list_tables().get("TableNames", []):
        if prefix in t.lower():
            found.append(f"dynamodb table {t}")

    for r in s.client("ecr").describe_repositories().get("repositories", []):
        if prefix in r["repositoryName"].lower():
            found.append(f"ecr repo {r['repositoryName']}")

    for sec in s.client("secretsmanager").list_secrets().get("SecretList", []):
        if prefix in sec["Name"].lower():
            found.append(f"secret {sec['Name']}")

    for sch in s.client("scheduler").list_schedules().get("Schedules", []):
        if prefix in sch["Name"].lower():
            found.append(f"schedule {sch['Name']}")

    if found:
        print("LEFTOVERS FOUND")
        for f in found:
            print(" -", f)
        raise SystemExit(1)
    print("clean: nothing matching", args.stack)


if __name__ == "__main__":
    main()

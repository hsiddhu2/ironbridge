#!/usr/bin/env python3
"""CDK app. Stack name and what to deploy come from cdk.json context.
Region: AWS_REGION, then AWS_DEFAULT_REGION, then cdk.json `region`, then us-east-1.
scripts/awsctx.py uses the same order. The account comes from the AWS profile in your shell."""

import os
from pathlib import Path

import aws_cdk as cdk
from stack import JclStack

app = cdk.App()
ctx = app.node.try_get_context
region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or ctx("region")
    or "us-east-1"
)
JclStack(
    app,
    ctx("stackName"),
    build_dir=(Path(__file__).parent / ctx("buildDir")).resolve(),
    sample_jobs=list(ctx("sampleJobs")),
    folder=ctx("deployFolder"),
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=region),
)
app.synth()

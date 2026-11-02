#!/usr/bin/env python3
"""CDK app. Region, stack name and what to deploy come from cdk.json context.
The account comes from the AWS profile in your shell."""

import os
from pathlib import Path

import aws_cdk as cdk
from stack import JclStack

app = cdk.App()
ctx = app.node.try_get_context
JclStack(
    app,
    ctx("stackName"),
    build_dir=(Path(__file__).parent / ctx("buildDir")).resolve(),
    sample_jobs=list(ctx("sampleJobs")),
    folder=ctx("deployFolder"),
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=ctx("region")),
)
app.synth()

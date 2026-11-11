import json
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from ironbridge.jcl.command import to_asl

INFRA = Path(__file__).resolve().parents[1]
FIX = INFRA.parents[2] / "fixtures" / "carddemo" / "app"
sys.path.insert(0, str(INFRA))
from stack import JclStack  # noqa: E402

CTX = json.loads((INFRA / "cdk.json").read_text())["context"]


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    out = tmp_path_factory.mktemp("build")
    to_asl(FIX / "jcl", FIX / "proc", FIX / "scheduler" / "CardDemo.controlm", out)
    app = cdk.App()
    stack = JclStack(
        app,
        CTX["stackName"],
        build_dir=out,
        sample_jobs=CTX["sampleJobs"],
        folder=CTX["deployFolder"],
        env=cdk.Environment(region=CTX["region"]),
    )
    return Template.from_stack(stack)


def test_state_machines(template):
    # TRANBKP, DEFGDGD, TRANREPT, plus CLOSEFIL, WAITSTEP, OPENFIL for the parent, plus parent
    template.resource_count_is("AWS::StepFunctions::StateMachine", 7)
    template.all_resources_properties(
        "AWS::StepFunctions::StateMachine", {"StateMachineType": "STANDARD"}
    )


def test_stub_settings(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Architectures": ["arm64"], "MemorySize": 128, "Timeout": 10},
    )


def test_schedule_disabled(template):
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {"State": "DISABLED", "ScheduleExpression": "cron(0 0 * * ? *)"},
    )


def test_log_retention(template):
    template.all_resources_properties("AWS::Logs::LogGroup", {"RetentionInDays": 7})


def test_wildcards_are_only_log_delivery_and_validate(template):
    allowed = {"states:ValidateStateMachineDefinition"}
    for kind in ("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"):
        for res in template.find_resources(kind).values():
            for st in res["Properties"]["PolicyDocument"]["Statement"]:
                if st["Resource"] == "*":
                    actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
                    assert all(a.startswith("logs:") or a in allowed for a in actions), actions


def test_parent_uses_sync2(template):
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    parent = next(v for k, v in sms.items() if k.startswith("Parent"))
    body = json.dumps(parent["Properties"]["DefinitionString"])
    assert "startExecution.sync:2" in body

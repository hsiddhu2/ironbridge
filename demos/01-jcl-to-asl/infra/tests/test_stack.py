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
def build(tmp_path_factory):
    out = tmp_path_factory.mktemp("build")
    owners = INFRA.parent / "owners.json"
    to_asl(FIX / "jcl", FIX / "proc", FIX / "scheduler" / "CardDemo.controlm", out, owners=owners)
    return out


def synth(build_dir: Path, **kw) -> Template:
    stack = JclStack(
        cdk.App(),
        CTX["stackName"],
        build_dir=build_dir,
        sample_jobs=CTX["sampleJobs"],
        folder=CTX["deployFolder"],
        env=cdk.Environment(region=CTX["region"]),
        **kw,
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def template(build):
    return synth(build)


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


def logical(template: Template, kind: str, prefix: str) -> str:
    return next(k for k in template.find_resources(kind) if k.startswith(prefix))


def state_machine_refs(template: Template) -> list[dict]:
    return [{"Ref": k} for k in template.find_resources("AWS::StepFunctions::StateMachine")]


def test_alert_rule_matches_failures_of_this_stack_only(template):
    rule = next(iter(template.find_resources("AWS::Events::Rule").values()))["Properties"]
    pattern = rule["EventPattern"]
    assert pattern["source"] == ["aws.states"]
    assert pattern["detail-type"] == ["Step Functions Execution Status Change"]
    assert pattern["detail"]["status"] == ["FAILED", "TIMED_OUT", "ABORTED"]
    # SUCCEEDED is not matched, so a redrive that succeeds sends no alert.
    assert sorted(map(json.dumps, pattern["detail"]["stateMachineArn"])) == sorted(
        map(json.dumps, state_machine_refs(template))
    )
    (target,) = rule["Targets"]
    assert target["RetryPolicy"] == {"MaximumEventAgeInSeconds": 7200, "MaximumRetryAttempts": 8}
    dlq = logical(template, "AWS::SQS::Queue", "AlertDlq")
    assert target["DeadLetterConfig"] == {"Arn": {"Fn::GetAtt": [dlq, "Arn"]}}
    assert target["Arn"] == {
        "Fn::GetAtt": [logical(template, "AWS::Lambda::Function", "Alert"), "Arn"]
    }


def test_alert_topic_is_kms_encrypted_and_tls_only(template):
    topic = next(iter(template.find_resources("AWS::SNS::Topic").values()))["Properties"]
    assert "alias/aws/sns" in json.dumps(topic["KmsMasterKeyId"])
    policy = next(iter(template.find_resources("AWS::SNS::TopicPolicy").values()))
    (deny,) = policy["Properties"]["PolicyDocument"]["Statement"]
    assert deny["Effect"] == "Deny" and deny["Condition"] == {
        "Bool": {"aws:SecureTransport": "false"}
    }
    template.resource_count_is("AWS::SNS::Subscription", 0)  # no address unless you pass one


def test_alert_email_is_opt_in(build):
    t = synth(build, alert_email="oncall@example.com")
    t.has_resource_properties(
        "AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "oncall@example.com"}
    )


def test_alert_dlq_and_lambda_failure_handling(template):
    template.has_resource_properties(
        "AWS::SQS::Queue", {"SqsManagedSseEnabled": True, "MessageRetentionPeriod": 1209600}
    )
    fn = template.find_resources("AWS::Lambda::Function")[
        logical(template, "AWS::Lambda::Function", "Alert")
    ]["Properties"]
    assert fn["Timeout"] == 30 and fn["MemorySize"] == 128 and "DeadLetterConfig" in fn
    assert "ReservedConcurrentExecutions" not in fn
    template.has_resource_properties(
        "AWS::Lambda::EventInvokeConfig",
        {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 7200},
    )


def test_alert_reserved_concurrency_is_opt_in(build):
    t = synth(build, alert_reserved_concurrency=2)
    t.has_resource_properties(
        "AWS::Lambda::Function",
        {"FunctionName": "ironbridge-m4-job-alert", "ReservedConcurrentExecutions": 2},
    )


def test_alert_role_is_least_privilege(template):
    role = logical(template, "AWS::IAM::Role", "AlertRole")
    policy = next(
        p["Properties"]
        for p in template.find_resources("AWS::IAM::Policy").values()
        if p["Properties"]["Roles"] == [{"Ref": role}]
    )
    by_action = {}
    for st in policy["PolicyDocument"]["Statement"]:
        actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        for a in actions:
            by_action[a] = res
    assert set(by_action) == {
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "states:GetExecutionHistory",
        "states:ListTagsForResource",
        "sns:Publish",
        "sqs:SendMessage",
    }
    history = json.dumps(by_action["states:GetExecutionHistory"])
    assert history.count(":execution:ironbridge-m4-") == 7 and '"*"' not in history
    assert sorted(map(json.dumps, by_action["states:ListTagsForResource"])) == sorted(
        map(json.dumps, state_machine_refs(template))
    )
    topic = logical(template, "AWS::SNS::Topic", "AlertTopic")
    assert by_action["sns:Publish"] == [{"Ref": topic}]
    dlq = logical(template, "AWS::SQS::Queue", "AlertDlq")
    assert by_action["sqs:SendMessage"] == [{"Fn::GetAtt": [dlq, "Arn"]}]


def tags_of(template: Template, prefix: str) -> dict[str, str]:
    sm = template.find_resources("AWS::StepFunctions::StateMachine")[
        logical(template, "AWS::StepFunctions::StateMachine", prefix)
    ]
    return {t["Key"]: t["Value"] for t in sm["Properties"]["Tags"]}


def test_owner_and_runbook_tags(template):
    owners = json.loads((INFRA.parent / "owners.json").read_text())
    runbook = owners["default"]["runbook"]
    tb = tags_of(template, "JobTRANBKP")
    assert tb["ironbridge:job"] == "TRANBKP"
    assert tb["ironbridge:owner"] == owners["jobs"]["TRANBKP"]["owner"]
    assert tb["ironbridge:owner-source"] == "owners-file"
    assert tb["ironbridge:runbook"] == runbook
    # CLOSEFIL is not in owners.json and its JOB card says NOTIFY=&SYSUID: no owner is guessed.
    cf = tags_of(template, "JobCLOSEFIL")
    assert (cf["ironbridge:owner"], cf["ironbridge:owner-source"]) == ("unassigned", "none")
    parent = tags_of(template, "Parent")
    assert parent["ironbridge:job"] == CTX["deployFolder"]

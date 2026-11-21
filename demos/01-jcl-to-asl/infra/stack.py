"""One stack for demo 01: three sample jobs, one Control-M folder parent, the jobs it calls,
the RC stub, a DISABLED schedule and failure alerts. Definitions and owners come from
`ironbridge jcl to-asl` in build/."""

from __future__ import annotations

import json
import re
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

STUB_DIR = Path(__file__).resolve().parents[3] / "ironbridge" / "jcl" / "stub"
ALERT_DIR = STUB_DIR.parent / "alerts"
# Every status that ends an execution without success. SUCCEEDED, RUNNING and
# PENDING_REDRIVE never alert, so a redrive that succeeds sends nothing.
ALERT_STATUSES = ["FAILED", "TIMED_OUT", "ABORTED"]
SYNC_RULE = "StepFunctionsGetEventsForStepFunctionsExecutionRule"
LOG_SKIP = [{"id": "CKV_AWS_158", "comment": "Service default encryption, per architecture"}]
STUB_SKIP = [
    {"id": "CKV_AWS_117", "comment": "No VPC resource is reached. A VPC adds ENIs and cost"},
    {"id": "CKV_AWS_116", "comment": "Invoked synchronously by Step Functions. No async DLQ"},
    {"id": "CKV_AWS_173", "comment": "Env vars hold step names only, no secrets"},
    {"id": "CKV_AWS_115", "comment": "Reserved concurrency fails in low-quota sandboxes"},
]
ALERT_SKIP = [
    {"id": "CKV_AWS_117", "comment": "Calls public AWS APIs only. A VPC adds ENIs and cost"},
    {"id": "CKV_AWS_173", "comment": "Env vars hold the topic ARN and log level, no secrets"},
]
DLQ_SSE_SKIP = {
    "id": "CKV_AWS_27",
    "comment": "Encrypted with SSE-SQS (SqsManagedSseEnabled). The check reads only "
    "KmsMasterKeyId. The AWS managed SQS key cannot grant EventBridge, which writes here",
}
NO_RESERVED = {
    "id": "CKV_AWS_115",
    "comment": "Opt in with -c alertReservedConcurrency=N. Any reservation fails in sandboxes "
    "whose account concurrency is 10",
}


def _skip(resource: Construct, checks: list[dict]) -> None:
    resource.node.default_child.cfn_options.metadata = {"checkov": {"skip": checks}}


def _tag_owner(sm: sfn.StateMachine, member: str, owners: dict) -> None:
    """Owner and runbook as tags, read by the alert Lambda and shown in the console."""
    entry = owners.get(member)
    if entry is None:
        raise SystemExit(f"build/owners.json has no entry for {member}. Run `make build` again.")
    cdk.Tags.of(sm).add("ironbridge:job", member)
    cdk.Tags.of(sm).add("ironbridge:owner", entry["owner"])
    cdk.Tags.of(sm).add("ironbridge:owner-source", entry["owner_source"])
    if entry["runbook"]:
        cdk.Tags.of(sm).add("ironbridge:runbook", entry["runbook"])


class JclStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        build_dir: Path,
        sample_jobs: list[str],
        folder: str,
        alert_email: str = "",
        alert_reserved_concurrency: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        cdk.Tags.of(self).add("project", construct_id)
        cdk.Tags.of(self).add("owner", "cloudwithharry")
        if not (build_dir / "coverage.json").exists():
            raise SystemExit(f"{build_dir} has no coverage.json. Run `make build` first.")
        owners_path = build_dir / "owners.json"
        if not owners_path.exists():
            raise SystemExit(f"{build_dir} has no owners.json. Run `make build` again.")
        owners = json.loads(owners_path.read_text())
        name = construct_id
        week = logs.RetentionDays.ONE_WEEK
        destroy = cdk.RemovalPolicy.DESTROY

        stub_logs = logs.LogGroup(self, "StubLogs", retention=week, removal_policy=destroy)
        stub_role = iam.Role(
            self, "StubRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )
        stub_logs.grant_write(stub_role)  # CreateLogStream and PutLogEvents on this group only
        stub = lambda_.Function(
            self,
            "Stub",
            function_name=f"{name}-rc-stub",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(STUB_DIR), exclude=["__pycache__", "*.pyc"]),
            # 128 MB: the stub parses a small event and returns an int. Nothing else runs.
            memory_size=128,
            # 10 s: a warm call takes milliseconds. 10 s covers a cold start with headroom
            # and still fails fast if something hangs.
            timeout=cdk.Duration.seconds(10),
            environment={"ABEND_STEPS": "", "LOG_LEVEL": "INFO"},
            role=stub_role,
            log_group=stub_logs,
            logging_format=lambda_.LoggingFormat.JSON,
        )
        _skip(stub, STUB_SKIP)

        sfn_logs = logs.LogGroup(
            self,
            "StatesLogs",
            log_group_name=f"/aws/vendedlogs/states/{name}",
            retention=week,
            removal_policy=destroy,
        )
        for group in (stub_logs, sfn_logs):
            _skip(group, LOG_SKIP)
        log_opts = sfn.LogOptions(
            destination=sfn_logs, level=sfn.LogLevel.ALL, include_execution_data=True
        )

        parent_path = build_dir / "folders" / f"{folder}.asl.json"
        parent_text = parent_path.read_text()
        called = re.findall(r"\$\{Job_([A-Z0-9@#$]+)\}", parent_text)
        jobs = list(dict.fromkeys(sample_jobs + called))

        # Job role: invoke the stub. CDK adds the CloudWatch Logs delivery actions on "*",
        # which the Step Functions docs require: https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
        job_role = iam.Role(
            self, "JobRole", assumed_by=iam.ServicePrincipal("states.amazonaws.com")
        )
        stub.grant_invoke(job_role)
        job_sms: dict[str, sfn.StateMachine] = {}
        for member in jobs:
            text = (build_dir / "jobs" / f"{member}.asl.json").read_text()
            job_sms[member] = sfn.StateMachine(
                self,
                f"Job{member}",
                state_machine_name=f"{name}-{member}",
                state_machine_type=sfn.StateMachineType.STANDARD,  # redrive needs Standard
                definition_body=sfn.DefinitionBody.from_string(text),
                definition_substitutions={"StubFunctionArn": stub.function_arn},
                role=job_role,
                logs=log_opts,
                removal_policy=destroy,
            )
            _tag_owner(job_sms[member], member, owners)
            cdk.CfnOutput(self, f"Job{member}Arn", value=job_sms[member].state_machine_arn)

        parent_name = f"{name}-{folder}"
        parent_role = iam.Role(
            self, "ParentRole", assumed_by=iam.ServicePrincipal("states.amazonaws.com")
        )
        parent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:StartExecution"],
                resources=[job_sms[m].state_machine_arn for m in called],
            )
        )
        parent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:DescribeExecution", "states:StopExecution"],
                resources=[self._execution_arn(f"{name}-{m}") for m in called],
            )
        )
        parent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["events:PutTargets", "events:PutRule", "events:DescribeRule"],
                resources=[
                    self.format_arn(service="events", resource="rule", resource_name=SYNC_RULE)
                ],
            )
        )
        # Parent role: CDK adds the same CloudWatch Logs delivery actions on "*"
        # because logs= is set. Required per
        # https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
        parent = sfn.StateMachine(
            self,
            "Parent",
            state_machine_name=parent_name,
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_string(parent_text),
            definition_substitutions={f"Job_{m}": job_sms[m].state_machine_arn for m in called},
            role=parent_role,
            logs=log_opts,
            removal_policy=destroy,
        )
        _tag_owner(parent, folder, owners)
        for m in called:
            parent.node.add_dependency(job_sms[m])

        sched = json.loads((build_dir / "schedules" / f"{folder}.json").read_text())
        sched_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        parent.grant_start_execution(sched_role)
        schedule = scheduler.CfnSchedule(
            self,
            "Schedule",
            name=parent_name,
            description=f"Control-M folder {folder}. Deployed DISABLED.",
            schedule_expression=sched["expression"],
            schedule_expression_timezone=sched["timezone"],
            state="DISABLED",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=parent.state_machine_arn, role_arn=sched_role.role_arn, input="{}"
            ),
        )

        every_sm = [job_sms[m].state_machine_arn for m in jobs] + [parent.state_machine_arn]
        every_exec = [self._execution_arn(f"{name}-{m}") for m in jobs]
        alerts = self._alerts(
            name,
            every_sm,
            every_exec + [self._execution_arn(parent_name)],
            alert_email,
            alert_reserved_concurrency,
        )
        operator = iam.ManagedPolicy(
            self,
            "OperatorPolicy",
            description="Attach to the identity that runs scripts/measure.py and scripts/run.py",
            statements=[
                # ValidateStateMachineDefinition has no resource type in the Service
                # Authorization Reference, so "*" is the only form IAM accepts.
                iam.PolicyStatement(
                    actions=["states:ValidateStateMachineDefinition"], resources=["*"]
                ),
                iam.PolicyStatement(actions=["states:StartExecution"], resources=every_sm),
                iam.PolicyStatement(
                    actions=[
                        "states:RedriveExecution",
                        "states:GetExecutionHistory",
                        "states:DescribeExecution",
                    ],
                    resources=every_exec + [self._execution_arn(parent_name)],
                ),
                iam.PolicyStatement(
                    actions=[
                        "lambda:UpdateFunctionConfiguration",
                        "lambda:GetFunctionConfiguration",
                    ],
                    resources=[stub.function_arn],
                ),
                # make alert-test: subscribe a temporary queue to the topic and read it.
                iam.PolicyStatement(
                    actions=["sns:Subscribe", "sns:Unsubscribe"],
                    resources=[alerts["topic"].topic_arn],
                ),
                iam.PolicyStatement(
                    actions=[
                        "sqs:CreateQueue",
                        "sqs:DeleteQueue",
                        "sqs:GetQueueUrl",
                        "sqs:GetQueueAttributes",
                        "sqs:SetQueueAttributes",
                        "sqs:TagQueue",
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                    ],
                    resources=[
                        self.format_arn(service="sqs", resource=f"{name}-alert-test-*"),
                    ],
                ),
                iam.PolicyStatement(
                    actions=["sqs:GetQueueAttributes"], resources=[alerts["dlq"].queue_arn]
                ),
            ],
        )
        cdk.CfnOutput(self, "StubFunctionName", value=stub.function_name)
        cdk.CfnOutput(self, "ParentArn", value=parent.state_machine_arn)
        cdk.CfnOutput(self, "ScheduleName", value=schedule.ref)
        cdk.CfnOutput(self, "OperatorPolicyArn", value=operator.managed_policy_arn)
        cdk.CfnOutput(self, "AlertTopicArn", value=alerts["topic"].topic_arn)
        cdk.CfnOutput(self, "AlertFunctionName", value=alerts["function"].function_name)
        cdk.CfnOutput(self, "AlertDlqUrl", value=alerts["dlq"].queue_url)
        cdk.CfnOutput(self, "AlertRuleName", value=alerts["rule"].rule_name)

    def _alerts(
        self,
        name: str,
        state_machines: list[str],
        executions: list[str],
        email: str,
        reserved: int | None,
    ) -> dict:
        """EventBridge rule on failed executions of this stack's state machines, a Lambda that
        finds the failed step, and an encrypted SNS topic. Event fields are documented at
        https://docs.aws.amazon.com/step-functions/latest/dg/eventbridge-integration.html"""
        destroy = cdk.RemovalPolicy.DESTROY
        # One DLQ for both failure paths: EventBridge could not invoke the Lambda, or the
        # Lambda failed every async attempt. SQS-managed encryption, TLS only, 14 days to look.
        dlq = sqs.Queue(
            self,
            "AlertDlq",
            queue_name=f"{name}-alerts-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=cdk.Duration.days(14),
            removal_policy=destroy,
        )
        _skip(dlq, [DLQ_SSE_SKIP])
        # The AWS managed key works here because the publisher is the Lambda role, not an AWS
        # service. A service publishing straight to the topic would need a customer managed key:
        # https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html
        topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=f"{name}-job-alerts",
            display_name="ironbridge job alerts",
            master_key=kms.Alias.from_alias_name(self, "SnsManagedKey", "alias/aws/sns"),
            enforce_ssl=True,
        )
        if email:
            topic.add_subscription(subs.EmailSubscription(email))

        logs_group = logs.LogGroup(
            self, "AlertLogs", retention=logs.RetentionDays.ONE_WEEK, removal_policy=destroy
        )
        _skip(logs_group, LOG_SKIP)
        role = iam.Role(self, "AlertRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        logs_group.grant_write(role)
        role.add_to_policy(
            iam.PolicyStatement(actions=["states:GetExecutionHistory"], resources=executions)
        )
        role.add_to_policy(
            iam.PolicyStatement(actions=["states:ListTagsForResource"], resources=state_machines)
        )
        role.add_to_policy(
            iam.PolicyStatement(actions=["sns:Publish"], resources=[topic.topic_arn])
        )
        fn = lambda_.Function(
            self,
            "Alert",
            function_name=f"{name}-job-alert",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(ALERT_DIR), exclude=["__pycache__", "*.pyc"]),
            # 128 MB: three small API calls and some string formatting. boto3 is in the runtime.
            memory_size=128,
            # 30 s: a cold start with boto3 plus up to 25 history pages on the longest history
            # (25,000 events). Typical runs read one page and finish in under a second.
            timeout=cdk.Duration.seconds(30),
            environment={"TOPIC_ARN": topic.topic_arn, "LOG_LEVEL": "INFO"},
            role=role,
            log_group=logs_group,
            logging_format=lambda_.LoggingFormat.JSON,
            dead_letter_queue=dlq,
            # Async retries for function errors, then the DLQ. Two is the Lambda default.
            retry_attempts=2,
            max_event_age=cdk.Duration.hours(2),
            reserved_concurrent_executions=reserved,
        )
        _skip(fn, ALERT_SKIP + ([] if reserved else [NO_RESERVED]))
        rule = events.Rule(
            self,
            "JobFailedRule",
            rule_name=f"{name}-job-failed",
            description="Failed, timed out or aborted executions of this stack's state machines",
            event_pattern=events.EventPattern(
                source=["aws.states"],
                detail_type=["Step Functions Execution Status Change"],
                detail={"status": ALERT_STATUSES, "stateMachineArn": state_machines},
            ),
        )
        # EventBridge retries a failed invoke (throttling, service errors) for up to 2 hours,
        # then sends the event to the DLQ. CDK adds the queue policy scoped to this rule.
        rule.add_target(
            targets.LambdaFunction(
                fn,
                dead_letter_queue=dlq,
                retry_attempts=8,
                max_event_age=cdk.Duration.hours(2),
            )
        )
        return {"topic": topic, "function": fn, "dlq": dlq, "rule": rule}

    def _execution_arn(self, sm_name: str) -> str:
        return self.format_arn(
            service="states",
            resource="execution",
            resource_name=f"{sm_name}:*",
            arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME,
        )

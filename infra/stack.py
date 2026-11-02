"""One stack for post m4: three sample jobs, one Control-M folder parent, the jobs it calls,
the RC stub and a DISABLED schedule. Definitions come from `ironbridge jcl to-asl` in build/."""

from __future__ import annotations

import json
import re
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

STUB_DIR = Path(__file__).resolve().parents[1] / "ironbridge" / "jcl" / "stub"
SYNC_RULE = "StepFunctionsGetEventsForStepFunctionsExecutionRule"
LOG_SKIP = [{"id": "CKV_AWS_158", "comment": "Service default encryption, per architecture"}]
STUB_SKIP = [
    {"id": "CKV_AWS_117", "comment": "No VPC resource is reached. A VPC adds ENIs and cost"},
    {"id": "CKV_AWS_116", "comment": "Invoked synchronously by Step Functions. No async DLQ"},
    {"id": "CKV_AWS_173", "comment": "Env vars hold step names only, no secrets"},
    {"id": "CKV_AWS_115", "comment": "Reserved concurrency fails in low-quota sandboxes"},
]


def _skip(resource: Construct, checks: list[dict]) -> None:
    resource.node.default_child.cfn_options.metadata = {"checkov": {"skip": checks}}


class JclStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        build_dir: Path,
        sample_jobs: list[str],
        folder: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        cdk.Tags.of(self).add("project", construct_id)
        cdk.Tags.of(self).add("owner", "cloudwithharry")
        if not (build_dir / "coverage.json").exists():
            raise SystemExit(f"{build_dir} has no coverage.json. Run `make build` first.")
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
        # which the Step Functions docs require (cw-logs.html).
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
            ],
        )
        cdk.CfnOutput(self, "StubFunctionName", value=stub.function_name)
        cdk.CfnOutput(self, "ParentArn", value=parent.state_machine_arn)
        cdk.CfnOutput(self, "ScheduleName", value=schedule.ref)
        cdk.CfnOutput(self, "OperatorPolicyArn", value=operator.managed_policy_arn)

    def _execution_arn(self, sm_name: str) -> str:
        return self.format_arn(
            service="states",
            resource="execution",
            resource_name=f"{sm_name}:*",
            arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME,
        )

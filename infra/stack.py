import aws_cdk as cdk
from aws_cdk import aws_logs as logs
from constructs import Construct


class PostStack(cdk.Stack):
    """One stack per post. Keep it small. Every resource gets a removal policy."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        cdk.Tags.of(self).add("project", construct_id)
        cdk.Tags.of(self).add("owner", "cloudwithharry")

        # Example: a log group with retention so nothing is unbounded.
        logs.LogGroup(
            self,
            "AppLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

"""Shared context for the scripts: stack name, region, outputs.json and AWS error handling.

Region order, the same one infra/app.py uses: AWS_REGION, then AWS_DEFAULT_REGION, then the
`region` key in infra/cdk.json context, then us-east-1. The measurement plan is only valid in
us-east-1, so measure.py flags any other region.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = json.loads((ROOT / "infra" / "cdk.json").read_text())["context"]
STACK = CONTEXT["stackName"]
DEFAULT_REGION = "us-east-1"


def resolve_region(env: dict | None = None, context: dict | None = None) -> str:
    env = os.environ if env is None else env
    context = CONTEXT if context is None else context
    return (
        env.get("AWS_REGION")
        or env.get("AWS_DEFAULT_REGION")
        or context.get("region")
        or DEFAULT_REGION
    )


REGION = resolve_region()


def session():
    import boto3
    from botocore.config import Config

    cfg = Config(retries={"mode": "standard", "max_attempts": 5})
    s = boto3.Session(region_name=REGION)
    return s, cfg


def outputs() -> dict:
    p = ROOT / "outputs.json"
    if not p.exists():
        raise SystemExit("outputs.json is missing. Run `make deploy` first.")
    out = json.loads(p.read_text())[STACK]
    deployed = next((v.split(":")[3] for v in out.values() if str(v).startswith("arn:")), REGION)
    if deployed != REGION:
        raise SystemExit(
            f"error: the stack in outputs.json is in {deployed} but the region is {REGION}. "
            f"Set AWS_REGION={deployed} or redeploy."
        )
    return out


# ClientError codes that mean "your credentials are wrong", not "the request is wrong".
AUTH_CODES = {
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "UnrecognizedClientException",
    "SignatureDoesNotMatch",
}


def auth_error_line(exc: BaseException) -> str | None:
    """One line for a credential or profile problem, None for anything else."""
    from botocore import exceptions as be

    profile = os.environ.get("AWS_PROFILE", "default")
    if isinstance(exc, be.ProfileNotFound):
        return f"error: AWS profile '{profile}' not found. Check AWS_PROFILE and ~/.aws/config."
    if isinstance(exc, be.SSOError | be.TokenRetrievalError):
        return f"error: SSO session for profile '{profile}' expired. Run: aws sso login"
    if isinstance(exc, be.NoCredentialsError | be.PartialCredentialsError):
        return "error: no AWS credentials found. Set AWS_PROFILE, for example AWS_PROFILE=sandbox."
    if isinstance(exc, be.CredentialRetrievalError):
        return f"error: could not load credentials for profile '{profile}': {exc}"
    if isinstance(exc, be.ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in AUTH_CODES:
            return f"error: AWS rejected the credentials for profile '{profile}' ({code})."
    return None


def guarded(main: Callable[[], int]) -> int:
    """Run a script's main and turn credential errors into one line and exit code 2."""
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a credential problem
        line = auth_error_line(exc)
        if line is None:
            raise
        print(line, file=sys.stderr)
        return 2

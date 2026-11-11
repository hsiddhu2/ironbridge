import sys
from pathlib import Path

from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import awsctx  # noqa: E402


def test_region_order():
    ctx = {"region": "us-east-1"}
    assert awsctx.resolve_region({}, ctx) == "us-east-1"
    assert awsctx.resolve_region({}, {}) == "us-east-1"
    assert awsctx.resolve_region({"AWS_DEFAULT_REGION": "eu-west-1"}, ctx) == "eu-west-1"
    env = {"AWS_REGION": "us-west-2", "AWS_DEFAULT_REGION": "eu-west-1"}
    assert awsctx.resolve_region(env, ctx) == "us-west-2"


def test_bad_profile_is_one_line_and_exit_2(capsys, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "doesnotexist")

    def boom():
        raise ProfileNotFound(profile="doesnotexist")

    assert awsctx.guarded(boom) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert err == [
        "error: AWS profile 'doesnotexist' not found. Check AWS_PROFILE and ~/.aws/config."
    ]


def test_credential_errors_map_and_others_reraise():
    assert awsctx.auth_error_line(NoCredentialsError()).startswith("error: no AWS credentials")
    expired = ClientError({"Error": {"Code": "ExpiredToken", "Message": "x"}}, "GetCallerIdentity")
    assert "ExpiredToken" in awsctx.auth_error_line(expired)
    other = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "x"}}, "StartExecution"
    )
    assert awsctx.auth_error_line(other) is None
    assert awsctx.auth_error_line(ValueError("x")) is None

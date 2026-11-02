import json
import sys
from pathlib import Path

import boto3
from botocore.stub import ANY, Stubber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure  # noqa: E402


def test_expected_redrive_duplicates_only_the_abend_step():
    path = measure.expected_redrive("TRANBKP")
    assert (
        path.count("STEP05") == 2 and len(path) == len(measure.EXPECTED[("TRANBKP", "all_rc0")]) + 1
    )


def test_substitutions_keep_jsonata_intact():
    text = '{"A": "${StubFunctionArn}", "B": "${Job_TRANBKP}", "C": "{% $x %}"}'
    out = json.loads(measure.substitutions(text, "us-east-1", "ACCOUNT"))
    assert out["A"].endswith(":function:ironbridge-m4-rc-stub")
    assert out["B"].endswith(":stateMachine:ironbridge-m4-TRANBKP")
    assert out["C"] == "{% $x %}"


def test_coverage_and_diff_phases(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "BUILD", tmp_path)
    rows = measure.phase_coverage()
    total = next(r for r in rows if r["construct"] == "TOTAL")
    assert total["total"] == 511 and set(measure.COLUMNS) == set(total)
    diff = measure.phase_diff()
    assert len(diff) == 6 and all(r["pct"] == 100.0 for r in diff)


def test_validate_with_stubbed_client(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "BUILD", tmp_path)
    measure.convert(measure.BUILD)
    client = boto3.client("stepfunctions", region_name="us-east-1")
    with Stubber(client) as stub:
        files = list((measure.BUILD / "jobs").glob("*.json")) + list(
            (measure.BUILD / "folders").glob("*.json")
        )
        for _ in files:
            stub.add_response(
                "validate_state_machine_definition",
                {"result": "OK", "diagnostics": [], "truncated": False},
                {"definition": ANY, "type": "STANDARD", "severity": "WARNING", "maxResults": 100},
            )
        rows = measure.phase_validate(client, "ACCOUNT")
    assert len(rows) == 43 and {r["validate_result"] for r in rows} == {"OK"}


def test_history_path_reads_entered_states():
    client = boto3.client("stepfunctions", region_name="us-east-1")
    events = [
        {
            "timestamp": 0,
            "type": "PassStateEntered",
            "id": 1,
            "stateEnteredEventDetails": {"name": "JOB"},
        },
        {
            "timestamp": 0,
            "type": "TaskStateEntered",
            "id": 2,
            "stateEnteredEventDetails": {"name": "STEP05"},
        },
    ]
    with Stubber(client) as stub:
        stub.add_response("get_execution_history", {"events": events}, {"executionArn": "x"})
        assert measure.history_path(client, "x") == (["JOB", "STEP05"], False)

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
        assert measure.history_path(client, "x") == (["JOB", "STEP05"], [], False)


FIXTURE = Path(__file__).parent / "fixtures" / "redrive-TRANBKP-history.json"


def stub_history(events):
    client = boto3.client("stepfunctions", region_name="us-east-1")
    stub = Stubber(client)
    stub.add_response("get_execution_history", {"events": events}, {"executionArn": "x"})
    return client, stub


def test_redrive_history_reproduces_b1_and_counts_the_rerun():
    """Recorded GetExecutionHistory of a TRANBKP abend at STEP05, cleared, then redriven."""
    events = json.loads(FIXTURE.read_text())["events"]
    # B1: redrive emits no second TaskStateEntered, so counting entered states finds no rerun.
    entered = [e["stateEnteredEventDetails"]["name"] for e in events if "StateEntered" in e["type"]]
    assert all(entered.count(n) == 1 for n in entered)
    assert [e["type"] for e in events].count("ExecutionRedriven") == 1
    client, stub = stub_history(events)
    with stub:
        path, rerun, throttled = measure.history_path(client, "x")
    assert rerun == ["STEP05"] and not throttled
    assert path == measure.expected_redrive("TRANBKP")


def test_task_retry_before_redrive_is_not_a_rerun():
    events = [
        {"timestamp": 0, "type": "TaskStateEntered", "id": 1,
         "stateEnteredEventDetails": {"name": "S1"}},
        {"timestamp": 0, "type": "TaskScheduled", "id": 2, "previousEventId": 1},
        {"timestamp": 0, "type": "TaskScheduled", "id": 3, "previousEventId": 2},
    ]  # fmt: skip
    client, stub = stub_history(events)
    with stub:
        assert measure.history_path(client, "x") == (["S1"], [], False)


def test_write_appends_with_one_run_id_per_invocation(tmp_path, monkeypatch):
    out = tmp_path / "results.csv"
    monkeypatch.setattr(measure, "RUN", "R1")
    measure.write([measure.row("diff", 1, job="A")], out)
    monkeypatch.setattr(measure, "RUN", "R2")
    measure.write([measure.row("diff", 1, job="A"), measure.row("diff", 2, job="B")], out)
    lines = out.read_text().splitlines()
    assert lines[0].split(",") == measure.COLUMNS and len(lines) == 4
    assert [ln.split(",")[0] for ln in lines[1:]] == ["R1-diff-001", "R2-diff-001", "R2-diff-002"]


def test_write_never_appends_to_a_file_with_other_columns(tmp_path, monkeypatch):
    out = tmp_path / "results.csv"
    out.write_text("a,b\n1,2\n")
    monkeypatch.setattr(measure, "RUN", "R3")
    written = measure.write([measure.row("diff", 1)], out)
    assert written.name == "results-R3.csv" and out.read_text() == "a,b\n1,2\n"

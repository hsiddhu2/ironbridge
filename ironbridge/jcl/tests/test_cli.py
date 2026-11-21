import json
from pathlib import Path

from ironbridge.cli import main
from ironbridge.core.report import MARKER

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "carddemo" / "app"


def run(out: Path) -> int:
    return main(
        [
            "jcl",
            "to-asl",
            "--jcl",
            str(FIX / "jcl"),
            "--proc",
            str(FIX / "proc"),
            "--controlm",
            str(FIX / "scheduler" / "CardDemo.controlm"),
            "--out",
            str(out),
        ]
    )


def snapshot(root: Path) -> dict[str, bytes]:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def test_two_runs_are_byte_identical(tmp_path):
    assert run(tmp_path / "a") == 0 and run(tmp_path / "b") == 0
    assert snapshot(tmp_path / "a") == snapshot(tmp_path / "b")


def test_marker_grep_equals_coverage(tmp_path):
    run(tmp_path)
    grep = sum(p.read_text().count(MARKER) for p in tmp_path.rglob("*.json"))
    cov = json.loads((tmp_path / "coverage.json").read_text())
    todos = cov["jcl"]["todo"] + cov["controlm"]["todo"]
    assert grep == cov["todo_markers_total"] == todos
    assert MARKER not in (tmp_path / "coverage.json").read_text()


def test_outputs(tmp_path):
    run(tmp_path)
    assert len(list((tmp_path / "jobs").glob("*.asl.json"))) == 38
    assert len(list((tmp_path / "folders").glob("*.asl.json"))) == 5
    cov = json.loads((tmp_path / "coverage.json").read_text())
    assert cov["jcl"]["total"] == 511 and cov["jcl"]["hard_subset"]["total"] == 29
    assert cov["reported_not_counted"] == {"RESTART= comments": 1, "IDCAMS IF LASTCC/MAXCC": 16}


def test_stale_output_is_removed(tmp_path):
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "OLD.asl.json").write_text("{}")
    run(tmp_path)
    assert not (tmp_path / "jobs" / "OLD.asl.json").exists()


def test_owners_json_from_notify(tmp_path):
    run(tmp_path)
    owners = json.loads((tmp_path / "owners.json").read_text())
    jobs = {k: v for k, v in owners.items() if v["kind"] == "job"}
    assert len(jobs) == 38 and sum(v["kind"] == "folder" for v in owners.values()) == 5
    # Every CardDemo JOB card says NOTIFY=&SYSUID, except the READCUST typo &SYUID.
    assert {k for k, v in jobs.items() if v["notify"] != "&SYSUID"} == {"READCUST"}
    assert {v["owner"] for v in owners.values()} == {"unassigned"}


def test_owners_file_sets_owner_and_keeps_headline(tmp_path, capsys):
    f = tmp_path / "owners.json"
    f.write_text(json.dumps({"jobs": {"TRANBKP": {"owner": "batch-oncall"}}}))
    out = tmp_path / "out"
    args = ["jcl", "to-asl", "--jcl", str(FIX / "jcl"), "--proc", str(FIX / "proc")]
    args += ["--controlm", str(FIX / "scheduler" / "CardDemo.controlm"), "--out", str(out)]
    assert main([*args, "--owners", str(f)]) == 0
    assert "JCL coverage 97.3% (497/511)" in capsys.readouterr().out
    owners = json.loads((out / "owners.json").read_text())
    assert owners["TRANBKP"]["owner"] == "batch-oncall"
    f.write_text(json.dumps({"jobs": {"TRANBKP": {"owner": "a&b"}}}))
    assert main([*args, "--owners", str(f)]) == 2
    assert "error: owners file" in capsys.readouterr().err

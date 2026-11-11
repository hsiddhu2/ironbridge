"""Run the demo 01 measurement plan (docs/measurement-plan.md) and append to results.csv.

Phases:
  coverage  offline. Runs to-asl twice, checks byte-identical output and the TODO grep.
  validate  ValidateStateMachineDefinition at severity WARNING for 38 jobs and 5 parents.
  diff      state type multiset and transition edges vs the hand-written baselines.
  run       deployed. 3 sample jobs x 3 scenarios: all RC 0, COND trip, abend then redrive.
  all       every phase in that order.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from awsctx import DEFAULT_REGION, REGION, ROOT, STACK, guarded, outputs, session

from ironbridge import __version__
from ironbridge.core.report import MARKER
from ironbridge.jcl.command import to_asl

# ROOT is this demo folder. REPO is the repository root, where the package and shared fixtures live.
REPO = ROOT.parents[1]
FIX = REPO / "fixtures" / "carddemo" / "app"
BASELINE = REPO / "ironbridge" / "jcl" / "tests" / "baseline"
BUILD = ROOT / "build"
CARDDEMO_COMMIT = "59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e"
COLUMNS = (
    "run_id, utc_time, phase, job, construct, total, translated, todo, pct, validate_result, "
    "warnings, scenario, execution_arn, path_match, redrive_count, steps_rerun, duration_ms, "
    "carddemo_commit, ironbridge_version, region"
).split(", ")
# Plan denominator, docs/measurement-plan.md. GDG relative refs corrected 15 to 17 (total 509 to
# 511) on 2026-11-01: grep and parser both find 15 (+1) and 2 (0).
RESEARCH = {
    "EXEC steps": 101,
    "Step COND=": 9,
    "GDG relative refs": 17,
    "GDG base definitions": 11,
    "EXEC PROC calls": 3,
    "JCLLIB": 3,
    "DD statements": 328,
    "Symbolic lines": 39,
}
# Paths read off the baselines. Run 2 trips COND where the job has one. TRANREPT has none.
SAMPLES = {
    "TRANBKP": {"trip": {"STEP05": 8}, "abend": "STEP05"},
    "DEFGDGD": {"trip": {"STEP10": 4}, "abend": "STEP30"},
    "TRANREPT": {"trip": {"STEP05R.PRC001": 4}, "abend": "STEP05R"},
}
EXPECTED = {
    ("TRANBKP", "all_rc0"): ["JOB", "STEP05R.PRC001", "STEP05", "COND STEP10", "STEP10", "EOJ"],
    ("TRANBKP", "cond_trip"): ["JOB", "STEP05R.PRC001", "STEP05", "COND STEP10", "EOJ"],
    ("DEFGDGD", "all_rc0"): [
        "JOB",
        "STEP10",
        "COND STEP20",
        "STEP20",
        "COND STEP30",
        "STEP30",
        "COND STEP40",
        "STEP40",
        "STEP50",
        "COND STEP60",
        "STEP60",
        "EOJ",
    ],
    ("DEFGDGD", "cond_trip"): [
        "JOB",
        "STEP10",
        "COND STEP20",
        "COND STEP30",
        "COND STEP40",
        "STEP50",
        "COND STEP60",
        "EOJ",
    ],
    ("TRANREPT", "all_rc0"): ["JOB", "STEP05R.PRC001", "STEP05R", "STEP10R", "EOJ"],
    ("TRANREPT", "cond_trip"): ["JOB", "STEP05R.PRC001", "STEP05R", "STEP10R", "EOJ"],
}


# One label per `measure.py` invocation. Every row in this run starts with it, so appended
# runs in results.csv can be told apart: run_id is <RUN>-<phase>-<nnn>.
RUN = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(phase: str, n: int, **kw) -> dict:
    base = {c: "" for c in COLUMNS}
    base.update(run_id=f"{RUN}-{phase}-{n:03d}", utc_time=now(), phase=phase)
    base.update(carddemo_commit=CARDDEMO_COMMIT, ironbridge_version=__version__, region=REGION)
    base.update({k: v for k, v in kw.items()})
    return base


def convert(out: Path):
    return to_asl(FIX / "jcl", FIX / "proc", FIX / "scheduler" / "CardDemo.controlm", out)


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.json"))}


def phase_coverage() -> list[dict]:
    rep = convert(BUILD).to_dict()
    with tempfile.TemporaryDirectory() as tmp:
        convert(Path(tmp))
        same = snapshot(BUILD) == snapshot(Path(tmp))
    grep = sum(p.read_text().count(MARKER) for p in BUILD.rglob("*.json"))
    rows, n = [], 0

    def add(construct: str, r: dict) -> None:
        nonlocal n
        n += 1
        pct = round(100.0 * r["translated"] / r["total"], 1) if r["total"] else 0.0
        rows.append(
            row(
                "coverage",
                n,
                construct=construct,
                total=r["total"],
                translated=r["translated"],
                todo=r["todo"],
                pct=pct,
            )
        )

    for name, r in rep["jcl"]["rows"].items():
        add(name, r)
    add("TOTAL", rep["jcl"])
    add("HARD_SUBSET COND+GDG+PROC", rep["jcl"]["hard_subset"])
    for name, r in rep["controlm"]["rows"].items():
        add(f"Control-M {name}", r)
    add("Control-M TOTAL", rep["controlm"])
    print(f"\nbyte-identical on rerun: {same}")
    print(f"TODO grep {grep} vs coverage.json {rep['todo_markers_total']}")
    print("parser vs plan denominator:")
    for name, r in rep["jcl"]["rows"].items():
        delta = r["total"] - RESEARCH[name]
        print(f"  {name:<22}{r['total']:>5} vs {RESEARCH[name]:>4}  delta {delta:+d}")
    print(f"reported, not counted: {rep['reported_not_counted']}")
    if not same or grep != rep["todo_markers_total"]:
        raise SystemExit("INVALID: output is not deterministic or the TODO grep does not match")
    return rows


def substitutions(text: str, region: str, account: str) -> str:
    """Fill ${...} placeholders with well-formed ARNs so Validate checks the real shape."""
    arn = f"arn:aws:lambda:{region}:{account}:function:{STACK}-rc-stub"
    text = text.replace("${StubFunctionArn}", arn)
    prefix = f"arn:aws:states:{region}:{account}:stateMachine:{STACK}-"
    return re.sub(r"\$\{Job_([A-Z0-9@#$]+)\}", lambda m: prefix + m.group(1), text)


def phase_validate(sfn=None, account: str | None = None) -> list[dict]:
    if sfn is None:
        s, cfg = session()
        sfn = s.client("stepfunctions", config=cfg)
        account = s.client("sts", config=cfg).get_caller_identity()["Account"]
    files = sorted((BUILD / "jobs").glob("*.asl.json")) + sorted((BUILD / "folders").glob("*.json"))
    rows = []
    for n, f in enumerate(files, 1):
        text = substitutions(f.read_text(), REGION, account)
        r = sfn.validate_state_machine_definition(
            definition=text, type="STANDARD", severity="WARNING", maxResults=100
        )
        warn = [d["code"] for d in r.get("diagnostics", []) if d["severity"] == "WARNING"]
        kind = "parent" if f.parent.name == "folders" else "job"
        rows.append(
            row(
                "validate",
                n,
                job=f.name.split(".")[0],
                construct=kind,
                validate_result=r["result"],
                warnings=f"{len(warn)}" + (f":{'|'.join(warn)}" if warn else ""),
            )
        )
    return rows


def edges(asl: dict) -> set[tuple[str, str]]:
    out = set()
    for name, st in asl["States"].items():
        targets = [st.get("Next"), st.get("Default")] + [c["Next"] for c in st.get("Choices", [])]
        out |= {(name, t) for t in targets if t}
    return out


def phase_diff() -> list[dict]:
    rows = []
    for n, job in enumerate(SAMPLES, 1):
        base = json.loads((BASELINE / f"{job}.asl.json").read_text())
        gen = json.loads((BUILD / "jobs" / f"{job}.asl.json").read_text())
        eb, eg = edges(base), edges(gen)
        tb = Counter(s["Type"] for s in base["States"].values())
        tg = Counter(s["Type"] for s in gen["States"].values())
        common = sum((tb & tg).values())
        union = sum((tb | tg).values())
        rows.append(
            row(
                "diff",
                2 * n - 1,
                job=job,
                construct="edges",
                total=len(eb),
                translated=len(eb & eg),
                pct=round(100.0 * len(eb & eg) / len(eb | eg), 1),
            )
        )
        rows.append(
            row(
                "diff",
                2 * n,
                job=job,
                construct="state_types",
                total=sum(tb.values()),
                translated=common,
                pct=round(100.0 * common / union, 1),
            )
        )
    return rows


def entered_state(by_id: dict[int, dict], e: dict) -> dict | None:
    """Walk previousEventId back to the *StateEntered event that owns this event."""
    while e is not None and not e["type"].endswith("StateEntered"):
        e = by_id.get(e.get("previousEventId"))
    return e


def history_path(sfn, arn: str) -> tuple[list[str], list[str], bool]:
    """State path, steps rerun by redrive, and whether any event mentions throttling.

    Redrive does not emit a second TaskStateEntered for the failed Task. Per the docs, "the
    execution reschedules and then reruns the Task state" and a Task "Schedules and starts the
    Task state again":
    https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html
    So a rerun is a TaskScheduled event after ExecutionRedriven whose owning TaskStateEntered
    came before that ExecutionRedriven. We put the state in the path again at that point.
    """
    events = []
    for page in sfn.get_paginator("get_execution_history").paginate(executionArn=arn):
        events += page["events"]
    by_id = {e["id"]: e for e in events}
    path, rerun, throttled, redriven_at = [], [], False, None
    for e in events:
        if e["type"] == "ExecutionRedriven":
            redriven_at = e["id"]
        elif e["type"].endswith("StateEntered"):
            path.append(e["stateEnteredEventDetails"]["name"])
        elif e["type"] == "TaskScheduled" and redriven_at is not None:
            owner = entered_state(by_id, e)
            if owner is not None and owner["id"] < redriven_at:
                name = owner["stateEnteredEventDetails"]["name"]
                path.append(name)
                rerun.append(name)
        details = json.dumps({k: v for k, v in e.items() if k.endswith("Details")}, default=str)
        throttled |= "Throttl" in details
    return path, rerun, throttled


def wait(sfn, arn: str) -> dict:
    while True:
        d = sfn.describe_execution(executionArn=arn)
        if d["status"] not in ("RUNNING", "PENDING_REDRIVE"):
            return d
        time.sleep(1)


def set_abend(lam, fn: str, value: str) -> None:
    env = lam.get_function_configuration(FunctionName=fn)["Environment"]["Variables"]
    env["ABEND_STEPS"] = value
    lam.update_function_configuration(FunctionName=fn, Environment={"Variables": env})
    lam.get_waiter("function_updated_v2").wait(FunctionName=fn)


def expected_redrive(job: str) -> list[str]:
    path = list(EXPECTED[(job, "all_rc0")])
    i = path.index(SAMPLES[job]["abend"])
    return path[: i + 1] + path[i:]


def phase_run() -> list[dict]:
    out = outputs()
    s, cfg = session()
    sfn, lam = s.client("stepfunctions", config=cfg), s.client("lambda", config=cfg)
    fn = out["StubFunctionName"]
    set_abend(lam, fn, "")
    rows, n, invalid = [], 0, []
    for job, spec in SAMPLES.items():
        arn = out[f"Job{job}Arn"]
        for scenario, sim in (("all_rc0", {}), ("cond_trip", spec["trip"]), ("abend_redrive", {})):
            n += 1
            if scenario == "abend_redrive":
                set_abend(lam, fn, f"{job}.{spec['abend']}")
            ex = sfn.start_execution(stateMachineArn=arn, input=json.dumps({"simulate": sim}))
            d = wait(sfn, ex["executionArn"])
            if scenario == "abend_redrive":
                if d["status"] != "FAILED":
                    invalid.append(f"{job} did not fail on the abend step")
                set_abend(lam, fn, "")  # clear, no deploy, then redrive the same execution
                sfn.redrive_execution(executionArn=ex["executionArn"])
                time.sleep(1)
                d = wait(sfn, ex["executionArn"])
            path, rerun, throttled = history_path(sfn, ex["executionArn"])
            if throttled:
                invalid.append(f"throttling in {ex['executionArn']}")
            want = (
                expected_redrive(job) if scenario == "abend_redrive" else EXPECTED[(job, scenario)]
            )
            want_rerun = [spec["abend"]] if scenario == "abend_redrive" else []
            if rerun != want_rerun:
                invalid.append(f"{job} {scenario} reran {rerun or 'nothing'}, want {want_rerun}")
            ms = int((d.get("stopDate", d["startDate"]) - d["startDate"]).total_seconds() * 1000)
            rows.append(
                row(
                    "run",
                    n,
                    job=job,
                    scenario=scenario,
                    execution_arn=ex["executionArn"],
                    path_match=str(
                        path == want and rerun == want_rerun and d["status"] == "SUCCEEDED"
                    ).lower(),
                    redrive_count=d.get("redriveCount", 0),
                    steps_rerun="|".join(rerun),  # B1: from TaskScheduled, not StateEntered
                    duration_ms=ms,
                    validate_result=d["status"],
                )
            )
    for msg in invalid:
        print("INVALID:", msg)
    return rows


def write(rows: list[dict], path: Path) -> Path:
    """Append to results.csv. A file with other columns is left alone and we write a new one."""
    if path.exists():
        with path.open() as f:
            header = next(csv.reader(f), [])
        if header != COLUMNS:
            path = path.with_name(f"{path.stem}-{RUN}{path.suffix}")
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return path


def summary(rows: list[dict]) -> None:
    for phase in dict.fromkeys(r["phase"] for r in rows):
        sel = [r for r in rows if r["phase"] == phase]
        print(f"\n== {phase} ({len(sel)} rows)")
        if phase in ("coverage", "diff"):
            for r in sel:
                label = f"{r['job']} {r['construct']}".strip()
                print(f"{label:<36}{r['total']:>6}{r['translated']:>6}{r['todo']:>6}{r['pct']:>8}")
        elif phase == "validate":
            for kind in ("job", "parent"):
                of = [r for r in sel if r["construct"] == kind]
                ok = sum(1 for r in of if r["validate_result"] == "OK")
                print(f"{kind}s OK {ok}/{len(of)}")
            for r in sel:
                if r["warnings"] != "0" or r["validate_result"] != "OK":
                    print(f"  {r['job']:<36}{r['validate_result']:>6}  warnings {r['warnings']}")
        else:
            for r in sel:
                print(
                    f"{r['job']:<10}{r['scenario']:<15}{r['validate_result']:<10}"
                    f"match={r['path_match']:<6}redrive={r['redrive_count']} "
                    f"rerun={r['steps_rerun'] or '-'} {r['duration_ms']} ms"
                )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("phase", choices=["coverage", "validate", "diff", "run", "all"])
    ap.add_argument("--out", type=Path, default=ROOT / "results.csv")
    args = ap.parse_args(argv)
    phases = ["coverage", "validate", "diff", "run"] if args.phase == "all" else [args.phase]
    if "coverage" not in phases and not (BUILD / "coverage.json").exists():
        convert(BUILD)
    funcs = {
        "coverage": phase_coverage,
        "validate": phase_validate,
        "diff": phase_diff,
        "run": phase_run,
    }
    if REGION != DEFAULT_REGION:
        print(f"WARNING: region {REGION}. The plan is only valid in {DEFAULT_REGION}.")
    rows = []
    for p in phases:
        rows += funcs[p]()
    out = write(rows, args.out)
    summary(rows)
    print(f"\nappended {len(rows)} rows with run_id prefix {RUN} to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded(main))

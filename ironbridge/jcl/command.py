"""`ironbridge jcl` subcommand group. v0.1.0 ships `to-asl`."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ironbridge import __version__
from ironbridge.core.report import Coverage
from ironbridge.jcl.controlm import emit_folder, load_folders
from ironbridge.jcl.emit import job_asl
from ironbridge.jcl.parser import HARD_SUBSET, ROWS, build_job, load_procs

OUTPUT_DIRS = ("jobs", "folders", "schedules")


def register(groups: argparse._SubParsersAction) -> None:
    jcl = groups.add_parser("jcl", help="JCL, PROC and Control-M to AWS Step Functions")
    sub = jcl.add_subparsers(dest="command", metavar="<command>", required=True)
    p = sub.add_parser("to-asl", help="emit ASL, Scheduler definitions and coverage.json")
    p.add_argument("--jcl", type=Path, required=True, help="folder of JCL members")
    p.add_argument("--proc", type=Path, help="folder of PROC members named by JCLLIB")
    p.add_argument("--controlm", type=Path, help="Control-M XML export")
    p.add_argument("--out", type=Path, default=Path("build"), help="output folder")
    p.add_argument("--timezone", default="UTC", help="IANA zone for Scheduler (default UTC)")
    p.set_defaults(func=_run)


def _dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def to_asl(
    jcl: Path, proc: Path | None, controlm: Path | None, out: Path, timezone: str = "UTC"
) -> Coverage:
    """Offline conversion. Output is byte-identical for the same input."""
    for d in OUTPUT_DIRS:
        shutil.rmtree(out / d, ignore_errors=True)
        (out / d).mkdir(parents=True)
    procs = load_procs(proc)
    members = sorted(
        (p for p in jcl.iterdir() if p.suffix.lower() == ".jcl"), key=lambda p: p.name.upper()
    )
    cov = Coverage(
        __version__,
        {"jcl_members": len(members), "proc_members": len(procs), "controlm": ""},
        ROWS,
        HARD_SUBSET,
    )
    gdg_catalog: dict[str, dict] = {}
    restart = idcams_if = 0
    for m in members:
        job = build_job(m, procs)
        cov.items += job.constructs
        restart += len(job.restart_comments)
        idcams_if += job.idcams_if
        for g in job.gdg_defs:
            gdg_catalog[g.pop("base")] = g
        _dump(out / "jobs" / f"{job.member}.asl.json", job_asl(job, __version__))
    cov.reported = {"RESTART= comments": restart, "IDCAMS IF LASTCC/MAXCC": idcams_if}
    if controlm:
        cov.source["controlm"] = controlm.name
        names = {m.stem.upper() for m in members}
        for f in load_folders(controlm):
            asl, sched, items = emit_folder(f, names, timezone, controlm.name)
            for c in items:
                cov.add(c, "controlm")
            _dump(out / "folders" / f"{f.name}.asl.json", asl)
            _dump(out / "schedules" / f"{f.name}.json", sched)
    _dump(out / "gdg-catalog.json", dict(sorted(gdg_catalog.items())))
    cov.write(out / "coverage.json")
    return cov


def _run(args: argparse.Namespace) -> int:
    cov = to_asl(args.jcl, args.proc, args.controlm, args.out, args.timezone)
    report = cov.to_dict()
    print(f"{'construct':<24}{'total':>7}{'done':>7}{'todo':>7}")
    for row, r in report["jcl"]["rows"].items():
        print(f"{row:<24}{r['total']:>7}{r['translated']:>7}{r['todo']:>7}")
    j, h = report["jcl"], report["jcl"]["hard_subset"]
    print(f"JCL coverage {j['pct']}% ({j['translated']}/{j['total']})")
    print(f"Hard subset COND, GDG, PROC {h['pct']}% ({h['translated']}/{h['total']})")
    if "controlm" in report:
        c = report["controlm"]
        print(f"Control-M coverage {c['pct']}% ({c['translated']}/{c['total']})")
    print(f"TODO markers {report['todo_markers_total']}. Wrote {args.out}/")
    return 0

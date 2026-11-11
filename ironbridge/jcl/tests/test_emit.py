import json
from pathlib import Path

import jsonata
import pytest

from ironbridge.jcl.emit import cond_expr, gdg_dsn, job_asl
from ironbridge.jcl.parser import build_job, load_procs
from ironbridge.jcl.tests.asl_walk import walk

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "carddemo" / "app"
BASE = Path(__file__).parent / "baseline"


def asl(member: str) -> dict:
    path = next(p for p in (FIX / "jcl").iterdir() if p.stem.upper() == member)
    return job_asl(build_job(path, load_procs(FIX / "proc")), "test")


def ev(expr: str, **env):
    return jsonata.Jsonata(expr[2:-2]).evaluate(None, env)


@pytest.mark.parametrize(
    "tests,rc,bypass",
    [
        ([(4, "LT", "")], {"A": 0, "B": 4}, False),
        ([(4, "LT", "")], {"A": 8}, True),
        ([(0, "NE", "")], {}, False),
        ([(0, "NE", "")], {"A": 0, "B": 1}, True),
        ([(8, "GT", "B")], {"A": 9}, False),
        ([(8, "GT", "B")], {"A": 9, "B": 4}, True),
        ([(4, "LT", ""), (0, "EQ", "B")], {"B": 0}, True),
    ],
)
def test_cond_semantics(tests, rc, bypass):
    assert bool(ev(cond_expr(tests), rc=rc)) is bypass


def test_gdg_key_prefix():
    assert ev(gdg_dsn("A.B", 1), gdg={"A.B": 3}) == "gdg/A.B/G0004V00/"
    assert ev(gdg_dsn("A.B", 0), gdg={"A.B": 3}) == "gdg/A.B/G0003V00/"
    assert ev(gdg_dsn("A.B", -1), gdg={"A.B": 3}) == "gdg/A.B/G0002V00/"


def test_no_catch_and_no_fail_in_jobs():
    for p in (FIX / "jcl").iterdir():
        text = json.dumps(asl(p.stem.upper()))
        assert '"Catch"' not in text and '"Fail"' not in text


def test_tranbkp_paths():
    d = asl("TRANBKP")
    path, out = walk(d, {})
    assert path == ["JOB", "STEP05R.PRC001", "STEP05", "COND STEP10", "STEP10", "EOJ"]
    assert out["gdg"] == {"AWS.M2.CARDDEMO.TRANSACT.BKUP": 1} and out["maxcc"] == 0
    path, out = walk(d, {"simulate": {"STEP05": 8}, "gdg": {"AWS.M2.CARDDEMO.TRANSACT.BKUP": 4}})
    assert path == ["JOB", "STEP05R.PRC001", "STEP05", "COND STEP10", "EOJ"]
    assert out["maxcc"] == 8 and out["gdg"]["AWS.M2.CARDDEMO.TRANSACT.BKUP"] == 5


def test_defgdgd_bypass_path():
    path, out = walk(asl("DEFGDGD"), {"simulate": {"STEP10": 4}})
    skipped = ["COND STEP20", "COND STEP30", "COND STEP40", "STEP50", "COND STEP60"]
    assert path == ["JOB", "STEP10", *skipped, "EOJ"]
    assert out["gdg"] == {
        "AWS.M2.CARDDEMO.TRANTYPE.BKUP": 0,
        "AWS.M2.CARDDEMO.TRANCATG.PS.BKUP": 0,
        "AWS.M2.CARDDEMO.DISCGRP.BKUP": 0,
    }


@pytest.mark.parametrize("member", ["TRANBKP", "DEFGDGD", "TRANREPT"])
def test_baseline_paths_match(member):
    gen, base = asl(member), json.loads((BASE / f"{member}.asl.json").read_text())
    for sim in ({}, {"STEP05": 8, "STEP10": 4}):
        assert walk(gen, {"simulate": sim})[0] == walk(base, {"simulate": sim})[0]


def test_every_jsonata_expression_compiles():
    def strings(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)
        elif isinstance(o, str) and o.startswith("{%"):
            yield o

    for p in (FIX / "jcl").iterdir():
        for s in strings(asl(p.stem.upper())):
            assert s.endswith("%}")
            jsonata.Jsonata(s[2:-2])

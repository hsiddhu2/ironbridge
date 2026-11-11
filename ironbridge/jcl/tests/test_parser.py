from collections import Counter
from pathlib import Path

import pytest

from ironbridge.jcl.parser import (
    ROWS,
    build_job,
    keywords,
    load_procs,
    parse_cond,
    read_statements,
    split_operands,
    substitute,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "carddemo" / "app"

# research.md counts at CardDemo 59cc6c2. GDG refs differ by 2. See engineer-report.md.
DENOMINATOR = {
    "EXEC steps": 101,
    "Step COND=": 9,
    "GDG relative refs": 17,
    "GDG base definitions": 11,
    "EXEC PROC calls": 3,
    "JCLLIB": 3,
    "DD statements": 328,
    "Symbolic lines": 39,
}


@pytest.fixture(scope="module")
def jobs():
    procs = load_procs(FIX / "proc")
    return {p.stem.upper(): build_job(p, procs) for p in sorted((FIX / "jcl").iterdir())}


def test_counts_match_denominator(jobs):
    rows = Counter(c.row for j in jobs.values() for c in j.constructs)
    assert {r: rows[r] for r in ROWS} == DENOMINATOR


def test_reported_not_counted(jobs):
    assert sum(len(j.restart_comments) for j in jobs.values()) == 1
    assert sum(j.idcams_if for j in jobs.values()) == 16


def test_operand_field_stops_at_blank_outside_quotes(tmp_path):
    p = tmp_path / "X.jcl"
    p.write_text(
        "//X JOB 'A B C',CLASS=A,                                               00010000\n"
        "//  NOTIFY=&SYSUID\n"
        "//S1 EXEC PGM=IEFBR14 COMMENT TEXT\n"
        "//DD1 DD SPACE=(CYL,(1,1)), junk after blank\n"
        "//      DSN=A.B\n"
    )
    stmts, _ = read_statements(p)
    assert [s.op for s in stmts] == ["JOB", "EXEC", "DD"]
    assert "".join(t for _, t in stmts[0].pieces) == "'A B C',CLASS=A,NOTIFY=&SYSUID"
    assert "".join(t for _, t in stmts[2].pieces) == "SPACE=(CYL,(1,1)),DSN=A.B"


def test_crlf_and_instream(jobs):
    esds = jobs["ESDSRRDS"]
    assert len(esds.steps) == 6
    sysin = esds.steps[2].dds["SYSIN"]
    assert sysin.instream and not any(x.endswith("\r") for x in sysin.instream)


def test_split_and_keywords():
    assert split_operands("A=(1,2),B='x,y',C") == ["A=(1,2)", "B='x,y'", "C"]
    pos, kw = keywords("PGM=SORT,COND=(0,NE),'x'")
    assert kw == {"PGM": "SORT", "COND": "(0,NE)"} and pos == ["'x'"]


def test_symbols():
    assert substitute("DSNAME01(&HLQ..LOADLIB)", {"HLQ": "A.B"}) == ("DSNAME01(A.B.LOADLIB)", [])
    assert substitute("NOTIFY=&SYSUID", {}) == ("NOTIFY=&SYSUID", [])
    assert substitute("NOTIFY=&SYUID", {})[1] == ["SYUID"]
    assert substitute("DSN=&&TEMP", {})[1] == []


def test_set_symbol_reaches_instream_with_symbols(jobs):
    lines = jobs["CBADMCDJ"].steps[0].dds["SYSIN"].instream
    assert any("DSNAME01(AWS.M2.CARDDEMO.LOADLIB)" in x for x in lines)


@pytest.mark.parametrize(
    "value,tests,todo",
    [
        ("(4,LT)", [(4, "LT", "")], False),
        ("(0,NE)", [(0, "NE", "")], False),
        ("((4,LT),(8,GT,STEP1))", [(4, "LT", ""), (8, "GT", "STEP1")], False),
        ("(4,LT,STEP1.PS1)", [(4, "LT", "STEP1.PS1")], False),
        ("EVEN", [], True),
        ("((4,LT),ONLY)", [(4, "LT", "")], True),
        ("(X,LT)", [], True),
    ],
)
def test_parse_cond(value, tests, todo):
    got, why = parse_cond(value)
    assert got == tests and bool(why) is todo


def test_proc_expansion_with_overrides(jobs):
    s = jobs["TRANBKP"].steps[0]
    assert s.name == "STEP05R.PRC001" and s.proc == "REPROC" and s.pgm == "IDCAMS"
    assert s.dds["FILEIN"].params["DSN"] == "AWS.M2.CARDDEMO.TRANSACT.VSAM.KSDS"
    assert s.dds["SYSIN"].params["DSN"] == "AWS.M2.CARDDEMO.CNTL(REPROCT)"
    assert s.dds["FILEOUT"].gdg == ("AWS.M2.CARDDEMO.TRANSACT.BKUP", 1)
    assert s.gdg_new == ["AWS.M2.CARDDEMO.TRANSACT.BKUP"]


def test_duplicate_step_names_stay_unique(jobs):
    assert [s.name for s in jobs["TRANREPT"].steps] == ["STEP05R.PRC001", "STEP05R", "STEP10R"]
    assert [s.name for s in jobs["DEFCUST"].steps] == ["STEP05", "STEP05_2"]


def test_concatenation_and_gdg_zero(jobs):
    sortin = jobs["COMBTRAN"].steps[0].dds["SORTIN"]
    assert sortin.gdg == ("AWS.M2.CARDDEMO.TRANSACT.BKUP", 0)
    assert sortin.concat[0].gdg == ("AWS.M2.CARDDEMO.SYSTRAN", 0)
    assert jobs["COMBTRAN"].steps[0].gdg_new == ["AWS.M2.CARDDEMO.TRANSACT.COMBINED"]


def test_gdg_definitions(jobs):
    defs = [d for j in jobs.values() for d in j.gdg_defs]
    assert len(defs) == 11
    rept = next(d for d in jobs["REPTFILE"].gdg_defs)
    assert rept["limit"] == 10 and rept["scratch"] is False


def test_todo_reasons(jobs):
    todos = {(c.job, c.row) for j in jobs.values() for c in j.constructs if not c.translated}
    assert ("INTRDRJ1", "DD statements") in todos
    assert ("READCUST", "Symbolic lines") in todos
    assert ("FTPJCL", "EXEC steps") in todos
    assert sum(1 for j in jobs.values() for c in j.constructs if not c.translated) == 14


def test_ftp_instream_is_redacted(jobs):
    assert "redacted" in jobs["FTPJCL"].steps[0].dds["SYSIN"].instream[0]


def test_missing_proc_is_todo(tmp_path):
    p = tmp_path / "J.jcl"
    p.write_text("//J JOB A\n//S1 EXEC PROC=NOPE\n//S1.DD1 DD DSN=A\n")
    job = build_job(p, {})
    rows = {c.row: c.translated for c in job.constructs}
    assert rows["EXEC PROC calls"] is False and rows["DD statements"] is False

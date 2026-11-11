from pathlib import Path

from ironbridge.jcl.controlm import cron, emit_folder, load_folders

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "carddemo" / "app"
XML = FIX / "scheduler" / "CardDemo.controlm"
MEMBERS = {p.stem.upper() for p in (FIX / "jcl").iterdir()}


def folders():
    return {f.name: f for f in load_folders(XML)}


def test_folder_names_are_unique():
    assert sorted(folders()) == [
        "DAILY-TransactionBackup",
        "MONTHLY-InterestCalculation",
        "WEEKLY-DisclosureGroupsRefresh",
        "WEEKLY-TransactionTypesDBRefresh",
        "WEEKLY-TransactionTypesDBRefresh-4",
    ]


def test_counts():
    fs = load_folders(XML)
    jobs = [j for f in fs for j in f.jobs]
    assert len(jobs) == 15
    assert sum(len(j.inconds) for j in jobs) == 12
    assert sum(len(j.outconds) for j in jobs) + sum(len(f.outconds) for f in fs) == 23


def test_daily_parent_is_a_resolved_chain():
    asl, sched, items = emit_folder(folders()["DAILY-TransactionBackup"], MEMBERS, "UTC", "x")
    order, name = [], asl["StartAt"]
    while "Next" in asl["States"][name]:
        name = asl["States"][name]["Next"]
        order.append(name)
    assert order == ["CLOSEFIL", "TRANBKP", "WAITSTEP", "OPENFIL", "END"]
    assert asl["States"]["TRANBKP"]["Resource"].endswith("startExecution.sync:2")
    assert all(i.translated for i in items if i.row in ("JOB", "INCOND", "OUTCOND"))
    assert sched["expression"] == "cron(0 0 * * ? *)" and sched["state"] == "DISABLED"


def test_unresolved_job_becomes_fail():
    asl, _, items = emit_folder(folders()["WEEKLY-TransactionTypesDBRefresh"], MEMBERS, "UTC", "x")
    fail = asl["States"]["MNTTRDB2"]
    assert fail["Type"] == "Fail" and fail["Error"] == "ironbridge.UnresolvedJob"
    assert any(i.row == "JOB" and not i.translated for i in items)


def test_cross_folder_incond_is_todo():
    _, _, items = emit_folder(folders()["WEEKLY-DisclosureGroupsRefresh"], MEMBERS, "UTC", "x")
    todo = [i for i in items if not i.translated and i.row in ("INCOND", "OUTCOND")]
    assert [(i.row, i.state) for i in todo] == [("INCOND", "CLOSEFIL"), ("OUTCOND", "FOLDER")]


def test_independent_jobs_run_in_parallel(tmp_path):
    xml = tmp_path / "p.xml"
    xml.write_text(
        '<DEFTABLE><FOLDER FOLDER_NAME="F">'
        '<JOB JOBNAME="A" MEMNAME="A"><OUTCOND NAME="A-OK" SIGN="+"/></JOB>'
        '<JOB JOBNAME="B" MEMNAME="B"><INCOND NAME="A-OK"/></JOB>'
        '<JOB JOBNAME="C" MEMNAME="C"><INCOND NAME="A-OK"/></JOB>'
        "</FOLDER></DEFTABLE>"
    )
    asl, _, _ = emit_folder(load_folders(xml)[0], {"A", "B", "C"}, "UTC", "p.xml")
    assert asl["States"]["A"]["Next"] == "LAYER 2"
    branches = asl["States"]["LAYER 2"]["Branches"]
    assert [b["StartAt"] for b in branches] == ["B", "C"]


def test_cron():
    assert cron({"DAYS": "ALL"}) == "cron(0 0 * * ? *)"
    assert cron({"DAYS": "SA"}) == "cron(0 0 ? * SAT *)"
    assert cron({"DAYS": "1,15", "TIMEFROM": "0230", "FEB": "0"}).startswith("cron(30 2 1,15 ")
    assert cron({}) is None

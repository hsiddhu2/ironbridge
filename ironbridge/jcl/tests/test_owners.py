import json

import pytest

from ironbridge.jcl.owners import UNASSIGNED, OwnersError, load_overrides, notify_of, resolve


def test_notify_from_job_card():
    assert notify_of("'DEF GDG',CLASS=A,MSGCLASS=0,NOTIFY=&SYSUID") == "&SYSUID"
    assert notify_of("CLASS=A,NOTIFY=OPSUSR1") == "OPSUSR1"
    assert notify_of("CLASS=A") == ""


def test_sysuid_symbol_is_unassigned_not_guessed():
    out = resolve({"TRANBKP": ("job", "&SYSUID")}, load_overrides(None))
    assert out["TRANBKP"] == {
        "kind": "job",
        "notify": "&SYSUID",
        "owner": UNASSIGNED,
        "owner_source": "none",
        "runbook": "",
    }


def test_literal_notify_user_becomes_owner():
    out = resolve({"J1": ("job", "OPSUSR1")}, load_overrides(None))
    assert out["J1"]["owner"] == "OPSUSR1" and out["J1"]["owner_source"] == "notify"


def test_precedence_file_then_notify_then_default(tmp_path):
    f = tmp_path / "owners.json"
    f.write_text(
        json.dumps(
            {
                "default": {"owner": "batch-ops", "runbook": "https://example.com/rb"},
                "jobs": {"A": {"owner": "team-a", "runbook": "https://example.com/a"}},
            }
        )
    )
    names = {"A": ("job", "OPSUSR1"), "B": ("job", "OPSUSR2"), "C": ("job", "&SYSUID")}
    out = resolve(names, load_overrides(f))
    assert (out["A"]["owner"], out["A"]["owner_source"]) == ("team-a", "owners-file")
    assert out["A"]["runbook"] == "https://example.com/a"
    assert (out["B"]["owner"], out["B"]["owner_source"]) == ("OPSUSR2", "notify")
    assert (out["C"]["owner"], out["C"]["owner_source"]) == ("batch-ops", "owners-file-default")
    assert out["B"]["runbook"] == out["C"]["runbook"] == "https://example.com/rb"


@pytest.mark.parametrize(
    "body",
    [
        {"jobs": {"A": {"owner": "a&b"}}},
        {"jobs": {"A": {"runbook": "https://x.example/a?b=1"}}},
        {"jobs": {"A": {"owner": 7}}},
        {"jobs": {"A": {"pager": "x"}}},
        {"owners": {}},
    ],
)
def test_bad_values_are_rejected(tmp_path, body):
    f = tmp_path / "owners.json"
    f.write_text(json.dumps(body))
    with pytest.raises(OwnersError):
        load_overrides(f)


def test_unknown_job_is_rejected(tmp_path):
    f = tmp_path / "owners.json"
    f.write_text(json.dumps({"jobs": {"NOSUCH": {"owner": "x"}}}))
    with pytest.raises(OwnersError, match="NOSUCH"):
        resolve({"A": ("job", "")}, load_overrides(f))


def test_missing_or_broken_file(tmp_path):
    with pytest.raises(OwnersError):
        load_overrides(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(OwnersError):
        load_overrides(tmp_path / "bad.json")

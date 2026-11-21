"""Job owner and runbook per state machine, written to build/owners.json.

The JOB card's NOTIFY= names the TSO user who gets a message when the job ends on z/OS.
CardDemo writes NOTIFY=&SYSUID on every job but READCUST, which has the typo &SYUID. &SYSUID is
a system symbol for the user who submitted the job, so z/OS fills it in at submit time. There
is no fixed person in the source.
On AWS the submitter is EventBridge Scheduler or whoever starts the execution, so ironbridge
does not invent one. A symbol gives the owner "unassigned". A literal user id is kept as is.

An optional owners file sets the owner and runbook by name. It wins over NOTIFY=.

    {
      "default": {"runbook": "https://..."},
      "jobs": {"TRANBKP": {"owner": "batch-oncall", "runbook": "https://..."}}
    }

Values become Step Functions tags, so they may only hold letters, digits, spaces and
_ . : / = + - @ (https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ironbridge.jcl.parser import keywords

UNASSIGNED = "unassigned"
TAG_VALUE = re.compile(r"[\w .:/=+\-@]{0,256}")
FIELDS = ("owner", "runbook")


class OwnersError(ValueError):
    """The owners file is missing, not JSON, or holds a value that cannot be a tag."""


def notify_of(card: str) -> str:
    """NOTIFY= value from the JOB card operands, or "" when the card has none."""
    return keywords(card)[1].get("NOTIFY", "")


def load_overrides(path: Path | None) -> dict:
    if path is None:
        return {"default": {}, "jobs": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise OwnersError(f"{path}: {exc}") from exc
    extra = set(data) - {"default", "jobs"}
    if extra:
        raise OwnersError(f"{path}: unknown top-level keys {sorted(extra)}")
    out = {"default": data.get("default", {}), "jobs": data.get("jobs", {})}
    for where, entry in [("default", out["default"]), *out["jobs"].items()]:
        bad = set(entry) - set(FIELDS)
        if bad:
            raise OwnersError(f"{path}: {where} has unknown fields {sorted(bad)}")
        for k, v in entry.items():
            if not isinstance(v, str) or not TAG_VALUE.fullmatch(v):
                raise OwnersError(
                    f"{path}: {where}.{k} must be a string of up to 256 letters, digits, "
                    "spaces or _ . : / = + - @ because it becomes a tag value"
                )
    return out


def resolve(names: dict[str, tuple[str, str]], overrides: dict) -> dict[str, dict]:
    """names maps a state machine name to (kind, NOTIFY value). Returns one entry per name."""
    unknown = sorted(set(overrides["jobs"]) - set(names))
    if unknown:
        raise OwnersError(f"owners file names {unknown}, which this build does not have")
    default = overrides["default"]
    out: dict[str, dict] = {}
    for name, (kind, notify) in sorted(names.items()):
        mine = overrides["jobs"].get(name, {})
        # A symbol such as &SYSUID has no value until z/OS fills it in at submit time.
        is_literal = bool(notify) and not notify.startswith("&") and TAG_VALUE.fullmatch(notify)
        literal = notify if is_literal else ""
        if mine.get("owner"):
            owner, source = mine["owner"], "owners-file"
        elif literal:
            owner, source = literal, "notify"
        elif default.get("owner"):
            owner, source = default["owner"], "owners-file-default"
        else:
            owner, source = UNASSIGNED, "none"
        out[name] = {
            "kind": kind,
            "notify": notify,
            "owner": owner,
            "owner_source": source,
            "runbook": mine.get("runbook") or default.get("runbook", ""),
        }
    return out

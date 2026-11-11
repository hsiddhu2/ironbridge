"""Control-M XML to one parent state machine per folder and one Scheduler definition per folder.

INCOND and OUTCOND inside a folder become the order of startExecution.sync:2 tasks. Jobs with
no dependency between them in the same layer run in a Parallel. A job with no JCL member
becomes a Fail state with error ironbridge.UnresolvedJob.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree

from ironbridge.core.report import Construct, todo
from ironbridge.jcl.emit import q

WEEKDAYS = dict(
    zip(
        ["SU", "MO", "TU", "WE", "TH", "FR", "SA"],
        ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"],
        strict=True,
    )
)
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


@dataclass
class CtmJob:
    name: str
    member: str
    attrs: dict
    inconds: list[str]
    outconds: list[tuple[str, str]]
    calendars: list[str]


@dataclass
class Folder:
    name: str
    attrs: dict
    jobs: list[CtmJob]
    outconds: list[tuple[str, str]] = field(default_factory=list)


def load_folders(path: Path) -> list[Folder]:
    root = ElementTree.parse(path).getroot()
    folders, seen = [], set()
    for el in root:
        if el.tag not in ("FOLDER", "SMART_FOLDER"):
            continue
        name = el.get("FOLDER_NAME", "FOLDER")
        if name in seen:  # CardDemo reuses one folder name for a regular and a SMART folder
            name = f"{name}-{el.get('REAL_FOLDER_ID', len(folders))}"
        seen.add(name)
        jobs = [
            CtmJob(
                j.get("JOBNAME", ""),
                j.get("MEMNAME", ""),
                dict(j.attrib),
                [c.get("NAME", "") for c in j.findall("INCOND")],
                [(c.get("NAME", ""), c.get("SIGN", "+")) for c in j.findall("OUTCOND")],
                [c.get("NAME", "") for c in j.findall("RULE_BASED_CALENDARS")],
            )
            for j in el.findall("JOB")
        ]
        outs = [(c.get("NAME", ""), c.get("SIGN", "+")) for c in el.findall("OUTCOND")]
        folders.append(Folder(name, dict(el.attrib), jobs, outs))
    return folders


def cron(attrs: dict) -> str | None:
    """DAYS plus month flags plus TIMEFROM to an EventBridge Scheduler cron expression."""
    days = attrs.get("DAYS", "")
    if not days:
        return None
    t = attrs.get("TIMEFROM", "0000")
    minute, hour = int(t[2:4] or 0), int(t[:2] or 0)
    months = [m for m in MONTHS if attrs.get(m, "1") == "1"]
    month = "*" if len(months) == 12 else ",".join(months)
    codes = days.split(",")
    if days == "ALL":
        dom, dow = "*", "?"
    elif all(c in WEEKDAYS for c in codes):
        dom, dow = "?", ",".join(WEEKDAYS[c] for c in codes)
    elif all(c.isdigit() for c in codes):
        dom, dow = ",".join(str(int(c)) for c in codes), "?"
    else:
        return None
    return f"cron({minute} {hour} {dom} {month} {dow} *)"


def emit_folder(
    f: Folder, members: set[str], timezone: str, file: str
) -> tuple[dict, dict, list[Construct]]:
    items: list[Construct] = []

    def rec(row: str, ok: bool, why: str, state: str) -> Construct:
        c = Construct(row, file, 0, f.name, ok, why, state)
        items.append(c)
        return c

    produced = {n: j.name for j in f.jobs for n, sign in j.outconds if sign == "+"}
    deps: dict[str, set[str]] = defaultdict(set)
    for j in f.jobs:
        for n in j.inconds:
            ok = n in produced
            if ok:
                deps[j.name].add(produced[n])
            rec("INCOND", ok, "" if ok else f"{n} comes from another folder", j.name)
        for _ in j.outconds:  # every job OUTCOND names a condition this folder owns
            rec("OUTCOND", True, "", j.name)
        for cal in j.calendars:
            ok = cal == "*"
            rec("RULE_BASED_CALENDARS", ok, "" if ok else f"calendar {cal} is not read", j.name)
        if j.attrs.get("TIMETO"):
            why = f"TIMETO={j.attrs['TIMETO']} ends a submission window. Scheduler has none"
            rec("TIMETO", False, why, j.name)
        ok = j.member in members
        rec("JOB", ok, "" if ok else f"MEMNAME {j.member} has no member in --jcl", j.name)
    for n, _sign in f.outconds:
        ok = n in produced
        rec("OUTCOND", ok, "" if ok else f"folder clears {n} from another folder", "FOLDER")

    layers, done = [], set()
    while len(done) < len(f.jobs):
        layer = [j for j in f.jobs if j.name not in done and deps[j.name] <= done]
        if not layer:  # a cycle. Run the rest in XML order
            layer = [j for j in f.jobs if j.name not in done]
        layers.append(layer)
        done |= {j.name for j in layer}

    markers: dict[str, list[str]] = defaultdict(list)
    states = {}
    for i, layer in enumerate(layers):
        nxt = layers[i + 1][0].name if i + 1 < len(layers) else "END"
        nxt = f"LAYER {i + 2}" if i + 1 < len(layers) and len(layers[i + 1]) > 1 else nxt
        if len(layer) == 1:
            states[layer[0].name] = _job_state(layer[0], members, nxt)
            if states[layer[0].name]["Type"] == "Fail":
                break
        else:
            states[f"LAYER {i + 1}"] = {
                "Type": "Parallel",
                "Comment": "Jobs with no condition between them run side by side.",
                "Branches": [
                    {"StartAt": j.name, "States": {j.name: _job_state(j, members, "")}}
                    for j in layer
                ],
                "Assign": {"gdg": "{% $merge($append([$gdg], $states.result.gdg)) %}"},
                "Next": nxt,
            }
    every = dict(states)
    for s in states.values():
        for b in s.get("Branches", []):
            every.update(b["States"])
    for c in items:
        if not c.translated and c.row != "SCHEDULE":
            where = c.state if c.state in every else "FOLDER"
            markers[where].append(todo(f"Control-M {c.row} on {c.state}. {c.reason}."))
    for key, ms in markers.items():
        if key in every:
            every[key]["Comment"] = " ".join([every[key]["Comment"], *ms])

    expr = cron(f.attrs) or next((cron(j.attrs) for j in f.jobs if not deps[j.name]), None)
    sched = {
        "folder": f.name,
        "expression": expr,
        "timezone": timezone,
        "state": "DISABLED",
        "comment": "Control-M orders this folder on its DAYS. No TIMEFROM means 00:00.",
    }
    if not expr:
        rec("SCHEDULE", False, "no DAYS on the folder or its first job", "schedule")
        sched["comment"] = todo("Control-M SCHEDULE. No DAYS on the folder or its first job.")
    else:
        rec("SCHEDULE", True, "", "schedule")

    first = next(iter(states), "END")
    folder_comment = " ".join([f"Control-M folder {f.name}.", *markers.get("FOLDER", [])])
    asl_states = {
        "FOLDER": {
            "Type": "Pass",
            "Comment": folder_comment,
            "Assign": {
                "gdg": "{% $exists($states.input.gdg) ? $states.input.gdg : {} %}",
                "sim": "{% $exists($states.input.simulate) ? $states.input.simulate : {} %}",
            },
            "Next": first,
        },
        **states,
    }
    if not any(s["Type"] == "Fail" for s in states.values()):
        asl_states["END"] = {
            "Type": "Pass",
            "Comment": "Folder complete. Returns the GDG map after every job.",
            "Output": {"folder": f.name, "gdg": "{% $gdg %}"},
            "End": True,
        }
    asl = {
        "Comment": f"Control-M folder {f.name} as one parent. Children run with .sync:2.",
        "QueryLanguage": "JSONata",
        "StartAt": "FOLDER",
        "States": asl_states,
    }
    return asl, sched, items


def _job_state(j: CtmJob, members: set[str], nxt: str) -> dict:
    desc = f"Control-M job {j.name} MEMNAME={j.member}. {j.attrs.get('DESCRIPTION', '')}".strip()
    if j.member not in members:
        return {
            "Type": "Fail",
            "Comment": desc,
            "Error": "ironbridge.UnresolvedJob",
            "Cause": f"{j.member} is in the Control-M folder but has no JCL member",
        }
    sim = f"$lookup($sim, {q(j.member)})"
    state = {
        "Type": "Task",
        "Comment": desc,
        "Resource": "arn:aws:states:::states:startExecution.sync:2",
        "Arguments": {
            "StateMachineArn": f"${{Job_{j.member}}}",
            "Input": {
                "gdg": "{% $gdg %}",
                "simulate": f"{{% $exists({sim}) ? {sim} : {{}} %}}",
                "AWS_STEP_FUNCTIONS_STARTED_BY_EXECUTION_ID": "{% $states.context.Execution.Id %}",
            },
        },
    }
    if nxt:
        state["Assign"] = {"gdg": "{% $merge([$gdg, $states.result.Output.gdg]) %}"}
        state["Next"] = nxt
    else:
        state["Output"] = "{% $states.result.Output %}"
        state["End"] = True
    return state

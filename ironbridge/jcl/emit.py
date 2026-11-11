"""Emit one Step Functions state machine (ASL, JSONata) per JCL job.

Shape: JOB (Pass) -> [COND <step> (Choice)] -> <step> (Task or Pass) ... -> EOJ (Pass).
No Catch and no Fail on step errors, so a failed step stays redrivable, like RESTART=.
"""

from __future__ import annotations

from collections import defaultdict

from ironbridge.core.report import todo
from ironbridge.jcl.parser import COND_OPS, DD, Job, Step

STUB = "${StubFunctionArn}"
# Lambda service errors only. An abend is a function error and is never retried.
LAMBDA_RETRY = [
    {
        "ErrorEquals": [
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
            "Lambda.SdkClientException",
            "Lambda.TooManyRequestsException",
        ],
        "IntervalSeconds": 1,
        "MaxAttempts": 3,
        "BackoffRate": 2,
        "JitterStrategy": "FULL",
    }
]


def q(s: str) -> str:
    """Quote a string for a JSONata literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def gdg_dsn(base: str, rel: int) -> str:
    """(0) is G(n), (+1) is G(n+1). Both become an S3 key prefix that post 3 binds."""
    gen = f"$lookup($gdg, {q(base)})" + (f" + {rel}" if rel > 0 else f" - {-rel}" if rel else "")
    return f"{{% {q(f'gdg/{base}/G')} & $formatInteger({gen}, '0000') & 'V00/' %}}"


def dd_json(dd: DD) -> dict | list:
    if dd.concat:
        return [dd_json(DD(dd.name, dd.params, dd.flags, dd.line, dd.instream, dd.gdg))] + [
            dd_json(c) for c in dd.concat
        ]
    out: dict = {"DUMMY": True} if "DUMMY" in dd.flags else {}
    for k, v in dd.params.items():
        if k in ("DSN", "DSNAME") and dd.gdg:
            v = gdg_dsn(*dd.gdg)
        out[k] = v
    if dd.instream is not None:
        out["instream"] = dd.instream
    return out


def cond_expr(tests: list[tuple[int, str, str]]) -> str:
    """COND bypasses the step when any test is true. No step name means every prior RC."""
    parts = []
    for code, op, step in tests:
        o = COND_OPS[op]
        if step:
            ref = f"$lookup($rc, {q(step)})"
            parts.append(f"($exists({ref}) and {code} {o} {ref})")
        else:
            parts.append(f"$count([$rc.*][{code} {o} $]) > 0")
    return "{% " + " or ".join(parts) + " %}"


def _comment(text: str, markers: list[str]) -> str:
    return " ".join([text, *markers]).strip()


def _assign(s: Step) -> dict:
    rc = "$states.result.Payload.rc" if s.pgm != "IEFBR14" else "0"
    out = {"rc": f"{{% $merge([$rc, {{{q(s.name)}: {rc}}}]) %}}"}
    if s.gdg_new:
        adv = ", ".join(f"{q(b)}: $lookup($gdg, {q(b)}) + 1" for b in s.gdg_new)
        out["gdgnew"] = f"{{% $merge([$gdgnew, {{{adv}}}]) %}}"
    return out


def step_state(job: Job, s: Step, nxt: str, markers: list[str]) -> dict:
    head = (
        f"PGM={s.pgm}"
        + (f" from PROC {s.proc}" if s.proc else "")
        + (f" PARM={s.parm}" if s.parm else "")
    )
    payload = {
        "job": job.member,
        "step": s.name,
        "pgm": s.pgm,
        "parm": s.parm,
        "dd": {name: dd_json(dd) for name, dd in s.dds.items()},
    }
    if s.pgm == "IEFBR14":
        # IEFBR14 does nothing and ends RC 0. Its DD dispositions are kept for post 3.
        return {
            "Type": "Pass",
            "Comment": _comment(head + ". IEFBR14 ends RC 0.", markers),
            "Output": payload,
            "Assign": _assign(s),
            "Next": nxt,
        }
    return {
        "Type": "Task",
        "Comment": _comment(head, markers),
        "Resource": "arn:aws:states:::lambda:invoke",
        "Arguments": {"FunctionName": STUB, "Payload": {**payload, "simulate": "{% $sim %}"}},
        "Retry": LAMBDA_RETRY,
        "Assign": _assign(s),
        "Next": nxt,
    }


def job_asl(job: Job, version: str) -> dict:
    names = {s.name for s in job.steps} | {f"COND {s.name}" for s in job.steps if s.cond}
    markers: dict[str, list[str]] = defaultdict(list)
    for c in job.constructs:
        if not c.translated:
            where = c.state if c.state in names else "JOB"
            markers[where].append(todo(f"{c.row} at {c.file}:{c.line}. {c.reason}."))

    def entry(i: int) -> str:
        if i >= len(job.steps):
            return "EOJ"
        s = job.steps[i]
        return f"COND {s.name}" if s.cond else s.name

    defaults = ", ".join(f"{q(b)}: 0" for b in job.gdg_bases)
    given = "$exists($states.input.gdg) ? $states.input.gdg : {}"
    note = [f"JCL comment {r!r}. Every step is a redrive point." for r in job.restart_comments]
    states: dict[str, dict] = {
        "JOB": {
            "Type": "Pass",
            "Comment": _comment(f"{job.name} JOB {job.card}", note + markers["JOB"]),
            "Assign": {
                "sim": "{% $exists($states.input.simulate) ? $states.input.simulate : {} %}",
                "gdg": f"{{% $merge([{{{defaults}}}, {given}]) %}}",
                "gdgnew": {},
                "rc": {},
                "notify": "{% $states.context.Execution.RoleArn %}",
            },
            "Next": entry(0),
        }
    }
    for i, s in enumerate(job.steps):
        if s.cond:
            key = f"COND {s.name}"
            states[key] = {
                "Type": "Choice",
                "Comment": _comment(f"COND on {s.name}. True bypasses the step.", markers[key]),
                "Choices": [{"Condition": cond_expr(s.cond), "Next": entry(i + 1)}],
                "Default": s.name,
            }
        states[s.name] = step_state(job, s, entry(i + 1), markers[s.name])
    states["EOJ"] = {
        "Type": "Pass",
        "Comment": "End of job. Returns step RCs, MAXCC and the advanced GDG map.",
        "Output": {
            "job": job.member,
            "rc": "{% $rc %}",
            "maxcc": "{% $count([$rc.*]) > 0 ? $max([$rc.*]) : 0 %}",
            "gdg": "{% $merge([$gdg, $gdgnew]) %}",
        },
        "End": True,
    }
    return {
        "Comment": f"CardDemo {job.file} as Step Functions. Generated by ironbridge {version}.",
        "QueryLanguage": "JSONata",
        "StartAt": "JOB",
        "States": states,
    }

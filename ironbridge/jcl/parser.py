"""Parse JCL members and PROCs into jobs, and record every countable construct.

Our own parser, written for the CardDemo dialect. Statement text is columns 1 to 71. Columns
73 to 80 are sequence numbers. The operand field ends at the first blank outside quotes.
A trailing comma continues the statement on the next `//` line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ironbridge.core.report import Construct

ROWS = [
    "EXEC steps",
    "Step COND=",
    "GDG relative refs",
    "GDG base definitions",
    "EXEC PROC calls",
    "JCLLIB",
    "DD statements",
    "Symbolic lines",
]
HARD_SUBSET = ["Step COND=", "GDG relative refs", "EXEC PROC calls"]

SYMBOL = re.compile(r"(?<!&)&([A-Z@#$][A-Z0-9@#$]{0,7})\.?")
# &SYSUID is the submitting user. The job runs as its execution role, so it maps to
# $states.context.Execution.RoleArn in the JOB state.
SYSTEM_SYMBOLS = {"SYSUID"}
GDG_REF = re.compile(r"^(?P<base>[^()]+)\((?P<rel>[+-]?\d+)\)$")
GDG_DEF = re.compile(
    r"DEFINE\s+(?:GENERATIONDATAGROUP|GDG)\s*\(\s*NAME\(([^)]+)\)((?:[^()]|\([^()]*\))*)\)"
)
IDCAMS_IF = re.compile(r"^\s*IF\s+(LASTCC|MAXCC)\b")
COND_OPS = {"GT": ">", "GE": ">=", "EQ": "=", "NE": "!=", "LT": "<", "LE": "<="}
INSTREAM_LIMIT = 256 * 1024
TODO_PGMS = {
    "SDSF": "PGM=SDSF sends console commands to CICS. No AWS mapping in v0.1.0",
    "FTP": "PGM=FTP moves a file to a remote host. Map it to AWS Transfer Family by hand",
    "DFHCSDUP": "PGM=DFHCSDUP updates the CICS CSD. Online resources are out of scope",
    "IKJEFT1B": "PGM=IKJEFT1B runs TSO and REXX. No AWS mapping in v0.1.0",
}
INTRDR_REASON = "writes JCL to the internal reader to submit another job. Model it as a parent"
FTP_REDACTED = ["(instream redacted by ironbridge: FTP control cards can hold host and password)"]


@dataclass
class Stmt:
    name: str
    op: str
    pieces: list[tuple[int, str]]
    line: int
    instream: list[tuple[int, str]] | None = None


@dataclass
class DD:
    name: str
    params: dict[str, str]
    flags: list[str]
    line: int
    instream: list[str] | None = None
    gdg: tuple[str, int] | None = None
    concat: list[DD] = field(default_factory=list)


@dataclass
class Step:
    name: str
    pgm: str
    parm: str
    line: int
    dds: dict[str, DD] = field(default_factory=dict)
    cond: list[tuple[int, str, str]] = field(default_factory=list)
    proc: str = ""
    gdg_new: list[str] = field(default_factory=list)


@dataclass
class Job:
    member: str
    file: str
    name: str = ""
    card: str = ""
    steps: list[Step] = field(default_factory=list)
    constructs: list[Construct] = field(default_factory=list)
    gdg_bases: list[str] = field(default_factory=list)
    gdg_defs: list[dict] = field(default_factory=list)
    restart_comments: list[str] = field(default_factory=list)
    idcams_if: int = 0


def _field(text: str) -> str:
    """Operand field: everything up to the first blank outside quotes."""
    quoted = False
    for i, c in enumerate(text):
        if c == "'":
            quoted = not quoted
        elif c == " " and not quoted:
            return text[:i]
    return text


def read_statements(path: Path) -> tuple[list[Stmt], list[str]]:
    """Return statements and the text of any `//*` comment that mentions RESTART=."""
    stmts: list[Stmt] = []
    restarts: list[str] = []
    data: list[tuple[int, str]] | None = None
    cur: Stmt | None = None
    cont = False
    for no, raw in enumerate(path.read_bytes().decode("latin-1").splitlines(), 1):
        raw = raw.rstrip("\r")
        if data is not None:
            if not raw.startswith(("/*", "//")):
                data.append((no, raw.rstrip()))
                continue
            data = None
            if raw.startswith("/*"):
                continue
        if raw.startswith("//*"):
            if "RESTART=" in raw:
                restarts.append(raw[3:71].strip())
            continue
        if not raw.startswith("//"):
            continue
        text = raw[2:71].rstrip()
        if cur is not None and cont:
            piece = _field(text.lstrip())
            cur.pieces.append((no, piece))
        elif not text.strip():
            break  # null statement ends the job
        else:
            name = "" if text.startswith(" ") else text.split(" ", 1)[0]
            op, _, rest = text[len(name) :].lstrip().partition(" ")
            piece = _field(rest.lstrip())
            cur = Stmt(name, op, [(no, piece)], no)
            stmts.append(cur)
        cont = piece.endswith(",")
        operands = "".join(p for _, p in cur.pieces)
        if not cont and cur.op == "DD" and re.match(r"(\*|DATA)(,|$)", operands):
            cur.instream = data = []
    return stmts, restarts


def split_operands(s: str) -> list[str]:
    out, cur, depth, quoted = [], [], 0, False
    for c in s:
        if c == "'":
            quoted = not quoted
        elif not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
        elif not quoted and c == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    if cur:
        out.append("".join(cur))
    return out


def keywords(s: str) -> tuple[list[str], dict[str, str]]:
    pos, kw = [], {}
    for tok in split_operands(s):
        k, eq, v = tok.partition("=")
        if eq and re.fullmatch(r"[A-Z@#$][A-Z0-9@#$.]*", k):
            kw[k] = v
        else:
            pos.append(tok)
    return pos, kw


def substitute(text: str, symbols: dict[str, str]) -> tuple[str, list[str]]:
    """Replace known &NAME symbols. Return the text and the names left unresolved."""
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in symbols:
            return symbols[name]
        if name not in SYSTEM_SYMBOLS:
            missing.append(name)
        return m.group(0)

    return SYMBOL.sub(repl, text), missing


def parse_cond(value: str) -> tuple[list[tuple[int, str, str]], str]:
    """COND=(code,op[,step]) or ((..),(..)). Returns tests and a TODO reason if any."""
    v = value.strip()
    if v in ("EVEN", "ONLY"):
        return [], f"COND={v} runs a step after an abend. Redrive has no equivalent"
    parts = split_operands(v[1:-1] if v.startswith("(") and v.endswith(")") else v)
    flags = [p for p in parts if p in ("EVEN", "ONLY")]
    groups = [p for p in parts if p not in flags]
    if groups and not groups[0].startswith("("):
        groups = [f"({','.join(groups)})"]
    tests = []
    for g in groups:
        t = split_operands(g.strip("()"))
        if len(t) not in (2, 3) or not t[0].isdigit() or t[1] not in COND_OPS:
            return [], f"COND={value} is not a form ironbridge reads"
        tests.append((int(t[0]), t[1], t[2] if len(t) == 3 else ""))
    reason = f"COND {','.join(flags)} runs a step after an abend. Not emitted" if flags else ""
    return tests, reason


def load_procs(proc_dir: Path | None) -> dict[str, list[Stmt]]:
    if not proc_dir:
        return {}
    files = sorted(p for p in proc_dir.iterdir() if p.is_file())
    return {p.stem.upper(): read_statements(p)[0] for p in files}


class _Builder:
    def __init__(self, path: Path, procs: dict[str, list[Stmt]]):
        self.job = Job(member=path.stem.upper(), file=path.name)
        self.procs = procs
        self.symbols: dict[str, str] = {}
        self.used: set[str] = set()
        self.step: Step | None = None
        self.proc_steps: list[Step] = []
        self.proc_calls: list[Construct] = []

    def rec(self, row: str, line: int, ok: bool, reason: str = "", state: str = "") -> Construct:
        c = Construct(row, self.job.file, line, self.job.member, ok, reason, state or "JOB")
        self.job.constructs.append(c)
        return c

    def unique(self, name: str) -> str:
        base, n = name or f"STEP{len(self.job.steps) + 1:03d}", 1
        name = base
        while name in self.used:
            n += 1
            name = f"{base}_{n}"
        self.used.add(name)
        return name

    def resolve(self, st: Stmt, symbols: dict, count: bool, state: str) -> tuple[str, list]:
        """Substitute symbols per physical line. Count member lines that carry a symbol."""
        text, missing_all = "", []
        for no, piece in st.pieces:
            out, missing = substitute(piece, symbols)
            text += out
            missing_all += missing
            if count and SYMBOL.search(piece):
                why = f"&{', &'.join(missing)} is not set by SET, PROC or EXEC" if missing else ""
                self.rec("Symbolic lines", no, not missing, why, state)
        return text, missing_all

    def build(self, path: Path) -> Job:
        stmts, self.job.restart_comments = read_statements(path)
        for st in stmts:
            self.statement(st)
        self.close_step()
        bad_calls = [c for c in self.proc_calls if not c.translated]
        for st in (s for s in stmts if s.op == "JCLLIB"):
            why = "a PROC it lists was not found in --proc" if bad_calls else ""
            self.rec("JCLLIB", st.line, not bad_calls, why)
        return self.job

    def statement(self, st: Stmt) -> None:
        state = self.step.name if self.step and st.op == "DD" else "JOB"
        if st.op == "EXEC":
            self.close_step()
            is_pgm = "PGM=" in "".join(p for _, p in st.pieces)
            state = self.unique(st.name) if is_pgm else st.name
        text, _ = self.resolve(st, self.symbols, True, state)
        pos, kw = keywords(text)
        if st.op == "JOB":
            self.job.name, self.job.card = st.name, text
        elif st.op == "SET":
            self.symbols.update(kw)
        elif st.op == "EXEC" and "PGM" in kw:
            self.step = Step(state, kw["PGM"], _parm(kw), st.line)
            self.add_cond(self.step, kw, st.line, [self.step])
            self.job.steps.append(self.step)
            self.proc_steps = []
        elif st.op == "EXEC":
            self.expand(state, kw.get("PROC") or (pos[0] if pos else ""), kw, st.line)
        elif st.op == "DD":
            self.add_dd(st, pos, kw)

    def add_cond(self, step: Step, kw: dict, line: int, targets: list[Step]) -> None:
        if "COND" not in kw:
            return
        tests, why = parse_cond(kw["COND"])
        for t in targets:
            t.cond = tests
        self.rec("Step COND=", line, not why, why, f"COND {targets[0].name}")

    def expand(self, state: str, name: str, kw: dict, line: int) -> None:
        stmts = self.procs.get(name.upper())
        why = "" if stmts else f"PROC {name} was not found in --proc"
        symbols = dict(self.symbols)
        steps: list[Step] = []
        for st in stmts or []:
            text, missing = self.resolve(st, symbols, False, state)
            pos, pkw = keywords(text)
            if st.op == "PROC":
                symbols.update({k: v for k, v in pkw.items() if k not in kw} | kw)
                symbols.pop("PROC", None)
            elif st.op == "EXEC":
                if "PGM" not in pkw:
                    why = why or f"PROC {name} calls another PROC. Nesting is not expanded"
                    continue
                s = Step(self.unique(f"{state}.{st.name}"), pkw["PGM"], _parm(pkw), line)
                s.proc = name
                steps.append(s)
            elif st.op == "DD" and steps:
                dd = self.make_dd(st, pos, pkw, count=False)
                steps[-1].dds[dd.name] = dd
            if missing:
                why = why or f"PROC {name} leaves &{missing[0]} unresolved"
        if not steps:
            steps = [Step(state, "", "", line, proc=name)]
            self.used.add(state)
        self.job.steps += steps
        first = steps[0].name
        c = self.rec("EXEC PROC calls", line, not why, why, first)
        self.proc_calls.append(c)
        self.rec("EXEC steps", line, not why, why, first)
        self.add_cond(steps[0], kw, line, steps)
        self.step, self.proc_steps = steps[-1], steps

    def make_dd(self, st: Stmt, pos: list[str], kw: dict, count: bool = True) -> DD:
        name = st.name.split(".")[-1]
        dd = DD(name, dict(kw), [p for p in pos if p in ("DUMMY", "*", "DATA")], st.line)
        m = GDG_REF.match(kw.get("DSN", kw.get("DSNAME", "")))
        if m:
            dd.gdg = (m["base"], int(m["rel"]))
        if st.instream is not None:
            lines = [t for _, t in st.instream]
            if "SYMBOLS" in kw:
                where = self.step.name if self.step else ""
                one = [Stmt("", "", [(n, t)], n) for n, t in st.instream]
                lines = [self.resolve(x, self.symbols, count, where)[0] for x in one]
            dd.instream = lines
        return dd

    def add_dd(self, st: Stmt, pos: list[str], kw: dict) -> None:
        dd = self.make_dd(st, pos, kw)
        why = ""
        target = self.step
        if "." in st.name:
            proc_step = st.name.split(".")[0]
            target = next((s for s in self.proc_steps if s.name.endswith("." + proc_step)), None)
            why = "" if target else f"override {st.name} names no step in the PROC"
        if target is None:
            self.rec("DD statements", st.line, False, why or "DD before any EXEC")
            return
        if "INTRDR" in kw.get("SYSOUT", ""):
            why = "SYSOUT=(A,INTRDR) " + INTRDR_REASON
        if dd.instream and sum(len(x) + 1 for x in dd.instream) > INSTREAM_LIMIT:
            dd.instream = dd.instream[:1000]
            why = "instream data over 256 KB was truncated"
        if dd.instream is not None and target.pgm == "FTP":
            dd.instream = FTP_REDACTED
        state = target.name
        if not st.name and target.dds:
            list(target.dds.values())[-1].concat.append(dd)
        elif "." in st.name and dd.name in target.dds:
            old = target.dds[dd.name]
            old.params.update(dd.params)
            old.gdg, old.flags, old.line = dd.gdg or old.gdg, dd.flags or old.flags, dd.line
            dd = old
        else:
            target.dds[dd.name] = dd
        self.rec("DD statements", st.line, not why, why, state)
        if dd.gdg:
            self.gdg(target, dd, st.line)

    def gdg(self, step: Step, dd: DD, line: int) -> None:
        base, rel = dd.gdg
        if base not in self.job.gdg_bases:
            self.job.gdg_bases.append(base)
        disp = dd.params.get("DISP", "NEW").strip("()").split(",")[0] or "NEW"
        if rel > 0 and disp == "NEW" and base not in step.gdg_new:
            step.gdg_new.append(base)
        self.rec("GDG relative refs", line, True, "", step.name)

    def close_step(self) -> None:
        s = self.step
        if s and not s.proc:
            why = TODO_PGMS.get(s.pgm, "")
            if any("INTRDR" in d.params.get("SYSOUT", "") for d in s.dds.values()):
                why = f"PGM={s.pgm} " + INTRDR_REASON
            self.rec("EXEC steps", s.line, not why, why, s.name)
        sysin = s.dds.get("SYSIN") if s and not s.proc and s.pgm == "IDCAMS" else None
        if sysin and sysin.instream:
            self.job.idcams_if += sum(1 for x in sysin.instream if IDCAMS_IF.match(x))
            body = " ".join(x.rstrip(" -") for x in sysin.instream)
            for m in GDG_DEF.finditer(body):
                opts = m.group(2)
                limit = re.search(r"LIMIT\((\d+)\)", opts)
                self.job.gdg_defs.append(
                    {
                        "base": m.group(1).strip(),
                        "limit": int(limit.group(1)) if limit else None,
                        "scratch": "NOSCRATCH" not in opts and "SCRATCH" in opts,
                        "empty": "NOEMPTY" not in opts and "EMPTY" in opts,
                        "job": self.job.member,
                        "step": s.name,
                    }
                )
                self.rec("GDG base definitions", s.line, True, "", s.name)
        self.step = None


def _parm(kw: dict) -> str:
    return kw.get("PARM", "").strip("'()")


def build_job(path: Path, procs: dict[str, list[Stmt]]) -> Job:
    return _Builder(path, procs).build(path)

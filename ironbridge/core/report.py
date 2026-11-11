"""Coverage report shared by all subcommands.

Every source construct is recorded once as translated or TODO. A TODO is written into the
output exactly once as `TODO(ironbridge): ...`, so a grep of the output equals the report count.
The report itself never contains the marker text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "TODO(ironbridge)"


def todo(reason: str) -> str:
    """Render one TODO marker. Use this and nothing else to write a marker."""
    return f"{MARKER}: {reason}"


@dataclass
class Construct:
    row: str
    file: str
    line: int
    job: str
    translated: bool
    reason: str = ""
    state: str = ""


@dataclass
class Coverage:
    tool_version: str
    source: dict
    rows: list[str]
    hard_subset: list[str]
    items: list[Construct] = field(default_factory=list)
    extra_items: dict[str, list[Construct]] = field(default_factory=dict)
    reported: dict[str, int] = field(default_factory=dict)

    def add(self, c: Construct, section: str = "") -> Construct:
        (self.extra_items.setdefault(section, []) if section else self.items).append(c)
        return c

    @staticmethod
    def table(items: list[Construct], rows: list[str]) -> dict:
        out = {}
        for r in rows:
            sel = [i for i in items if i.row == r]
            ok = sum(1 for i in sel if i.translated)
            out[r] = {"total": len(sel), "translated": ok, "todo": len(sel) - ok}
        return out

    @staticmethod
    def summary(items: list[Construct]) -> dict:
        total = len(items)
        ok = sum(1 for i in items if i.translated)
        pct = round(100.0 * ok / total, 1) if total else 0.0
        return {"total": total, "translated": ok, "todo": total - ok, "pct": pct}

    def todo_count(self) -> int:
        every = self.items + [i for v in self.extra_items.values() for i in v]
        return sum(1 for i in every if not i.translated)

    def to_dict(self) -> dict:
        hard = [i for i in self.items if i.row in self.hard_subset]
        sections = {}
        for name, items in sorted(self.extra_items.items()):
            rows = sorted({i.row for i in items})
            sections[name] = {
                "rows": self.table(items, rows),
                **self.summary(items),
                "todos": [_todo_entry(i) for i in items if not i.translated],
            }
        return {
            "tool": "ironbridge",
            "version": self.tool_version,
            "source": self.source,
            "jcl": {
                "rows": self.table(self.items, self.rows),
                **self.summary(self.items),
                "hard_subset": {"rows": self.hard_subset, **self.summary(hard)},
                "todos": [_todo_entry(i) for i in self.items if not i.translated],
            },
            **sections,
            "reported_not_counted": self.reported,
            "todo_markers_total": self.todo_count(),
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")


def _todo_entry(c: Construct) -> dict:
    return {
        "row": c.row,
        "job": c.job,
        "file": c.file,
        "line": c.line,
        "state": c.state,
        "reason": c.reason,
    }

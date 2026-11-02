"""`ironbridge` entry point. Subcommand groups are discovered from the `ironbridge.commands`
entry point group, so each package (jcl now, vsam, cobol, ebcdic, cics, transform later)
registers itself without this file changing."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import entry_points

from ironbridge import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ironbridge", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"ironbridge {__version__}")
    groups = parser.add_subparsers(dest="group", metavar="<group>", required=True)
    for ep in sorted(entry_points(group="ironbridge.commands"), key=lambda e: e.name):
        ep.load()(groups)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())

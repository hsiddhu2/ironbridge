"""Shared context for the scripts: stack name and region from infra/cdk.json, outputs.json."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = json.loads((ROOT / "infra" / "cdk.json").read_text())["context"]
STACK = CONTEXT["stackName"]
REGION = CONTEXT["region"]


def session():
    import boto3
    from botocore.config import Config

    cfg = Config(retries={"mode": "standard", "max_attempts": 5})
    s = boto3.Session(region_name=REGION)
    return s, cfg


def outputs() -> dict:
    p = ROOT / "outputs.json"
    if not p.exists():
        raise SystemExit("outputs.json is missing. Run `make deploy` first.")
    return json.loads(p.read_text())[STACK]

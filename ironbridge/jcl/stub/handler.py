"""RC stub for generated jobs. Post 3 replaces it with the real program runner.

Returns {"rc": n} for a step. n comes from event["simulate"][step], default 0.
Raises Abend for any "JOB.STEP" listed in the ABEND_STEPS environment variable, so the
execution fails unhandled and can be redriven from that step.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class Abend(Exception):
    """A z/OS abend. Step Functions sees errorType "Abend" and the execution fails."""


def _log(**fields) -> None:
    logger.info(json.dumps(fields, sort_keys=True))


def handler(event: dict, context) -> dict:
    job, step = str(event.get("job", "")), str(event.get("step", ""))
    key = f"{job}.{step}"
    abends = {s.strip() for s in os.environ.get("ABEND_STEPS", "").split(",") if s.strip()}
    request_id = getattr(context, "aws_request_id", "")
    if key in abends:
        _log(event="abend", job=job, step=step, pgm=event.get("pgm"), request_id=request_id)
        raise Abend(f"S0C7 simulated in {key}")
    simulate = event.get("simulate") or {}
    rc = int(simulate.get(step, 0)) if isinstance(simulate, dict) else 0
    _log(event="step", job=job, step=step, pgm=event.get("pgm"), rc=rc, request_id=request_id)
    return {"job": job, "step": step, "rc": rc}

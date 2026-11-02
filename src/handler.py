"""Lambda handler template with structured JSON logging."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def handler(event: dict, context) -> dict:
    logger.info(json.dumps({"event": "invoke", "keys": list(event.keys())}))
    return {"ok": True}

"""What PeakCut writes to its log: one line per event, with the time and where it came from.

PEAKCUT_LOG_LEVEL picks how much (INFO by default). PEAKCUT_LOG_JSON=1 writes each line as JSON, which log
services on a server can search and count; otherwise it is plain text for reading."""
import json
import logging
import os
import sys
import time


class _Json(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        out = {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + "Z", "level": r.levelname,
               "where": r.name, "msg": r.getMessage()}
        if r.exc_info:
            out["error"] = self.formatException(r.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup():
    """Set the log up once. Safe to call again."""
    root = logging.getLogger("peakcut")
    if getattr(root, "_set_up", False):
        return root
    level = getattr(logging, (os.environ.get("PEAKCUT_LOG_LEVEL") or "INFO").strip().upper(), logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    as_json = (os.environ.get("PEAKCUT_LOG_JSON") or "").strip().lower() in ("1", "true", "yes", "on")
    h.setFormatter(_Json() if as_json else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(h)
    root.setLevel(level)
    root.propagate = False
    root._set_up = True
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger("peakcut." + name)

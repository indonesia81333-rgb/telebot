import logging
import time
from collections import deque

START_TIME = time.time()
STATS = {"uploads": 0, "downloads": 0, "active": 0, "errors": 0}
ERRORS = deque(maxlen=50)  # newest last


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def human_time(seconds):
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}d {h}h {m}m {s}s" if d else f"{h}h {m}m {s}s"


def add_error(text):
    STATS["errors"] += 1
    ERRORS.append({"time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "error": str(text)[:500]})


class ErrorCapture(logging.Handler):
    """Copies every ERROR log record into the dashboard error list."""

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            msg = record.getMessage()
            if record.exc_info and record.exc_info[1]:
                msg += f": {record.exc_info[1]!r}"
            add_error(msg)

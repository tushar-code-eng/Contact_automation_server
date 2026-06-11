import threading

# Each job thread stores its own log function here
_local = threading.local()


def set_logger(fn):
    """Call this at the start of each job thread with a function that accepts (msg: str)."""
    _local.log_fn = fn


def log(msg: str):
    fn = getattr(_local, "log_fn", print)
    fn(str(msg))


def error(msg: str):
    fn = getattr(_local, "log_fn", print)
    fn(f"ERROR: {msg}")

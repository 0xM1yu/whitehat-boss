"""Slicer exception types, in a module of their own (#363).

`slicer.py` run as a script (`python slicer.py`, the documented way) is the module `__main__`,
and `slicer_pipeline` used to reach `QueryError` through a deferred `from slicer import
QueryError`. That import loads `slicer.py` a SECOND time under the name `slicer`, so the
pipeline's `except QueryError` named a different class than the one `slicer.py` raised: the
per-candidate isolation (#219) and the degraded fallback (#307) never ran, and one query
deadline aborted the whole run. A module that is only ever imported -- never run -- has exactly
one copy, so both sides now name the same class.
"""


class QueryError(Exception):
    def __init__(self, message: str, logs: str = "", *, poisoned: bool = False):
        super().__init__(message)
        self.logs = logs
        self.poisoned = poisoned

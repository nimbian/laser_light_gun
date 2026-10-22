"""Route print() output to the GUI log pane and a log file.

The rest of the package reports through print() (it's also used from the
CLIs). In the windowed exe there is no console (sys.stdout is None), so the GUI
replaces stdout/stderr with a stream that appends to the log file and emits
each line as a Qt signal, which is safe to emit from engine and serial threads.
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal

MAX_LOG_BYTES = 1_000_000


class LogStream(QObject):
    line = Signal(str)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.unlink()  # start fresh rather than grow forever
        self._file = open(path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._partial = ""

    def write(self, text: str) -> int:
        with self._lock:
            self._file.write(text)
            self._partial += text
            *lines, self._partial = self._partial.split("\n")
        for ln in lines:
            if ln.strip():
                self.line.emit(ln)
        return len(text)

    def flush(self) -> None:
        self._file.flush()

    def isatty(self) -> bool:
        return False


def install(path: Path) -> LogStream:
    stream = LogStream(path)
    sys.stdout = stream  # type: ignore[assignment]
    sys.stderr = stream  # type: ignore[assignment]

    def hook(exc_type, exc, tb):
        stream.write("".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)
    return stream

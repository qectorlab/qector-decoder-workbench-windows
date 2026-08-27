"""logger.py  -  Logging infrastructure for QECTOR Workbench."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional


def _resolve_log_dir() -> Path:
    """Resolve the log directory, never raising.

    QECTOR_LOG_DIR overrides everything; otherwise logs live in the per-user
    data directory (utils.get_data_dir()/"logs") so the app stays writable
    when installed under read-only locations such as Program Files.
    """
    try:
        override = os.environ.get("QECTOR_LOG_DIR", "").strip()
        if override:
            return Path(override)
    except Exception:
        pass
    try:
        from utils import get_data_dir

        return get_data_dir() / "logs"
    except Exception:
        return Path(".")


class _QectorLogger:
    """Application logger with file and console output."""

    def __init__(self):
        log_dir = _resolve_log_dir()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log_dir = Path(".")
        self.log_file = log_dir / "qector.log"
        self._logger = logging.getLogger("qector")
        self._logger.setLevel(logging.DEBUG)
        if not self._logger.handlers:
            try:
                fh = logging.FileHandler(str(self.log_file), encoding="utf-8")
                fh.setLevel(logging.DEBUG)
                fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
                self._logger.addHandler(fh)
            except Exception:
                pass
            try:
                sh = logging.StreamHandler(sys.stderr)
                sh.setLevel(logging.INFO)
                sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
                self._logger.addHandler(sh)
            except Exception:
                pass

    def info(self, msg: str) -> None:
        try:
            self._logger.info(msg)
        except Exception:
            pass

    def warning(self, msg: str) -> None:
        try:
            self._logger.warning(msg)
        except Exception:
            pass

    def error(self, msg: str) -> None:
        try:
            self._logger.error(msg)
        except Exception:
            pass

    def debug(self, msg: str) -> None:
        try:
            self._logger.debug(msg)
        except Exception:
            pass


_logger_instance: Optional[_QectorLogger] = None


def get_logger() -> _QectorLogger:
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = _QectorLogger()
    return _logger_instance

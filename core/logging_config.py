from __future__ import annotations

import logging
import sys
import threading

_configured = False
_lock = threading.Lock()


def configure_logging(level: str = "INFO") -> None:
    global _configured
    with _lock:
        if _configured:
            return
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stdout,
        )
        _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"convertai.{name}")

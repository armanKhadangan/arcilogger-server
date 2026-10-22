"""Endpoint modules. Every module here defines ``router = APIRouter(prefix=..., tags=[...])``
and is mounted under ``/api/v1`` automatically, in module-name order — adding a feature never
means editing ``app.py``. Prefixes must be unique (a test checks it).
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter


def discover() -> list[APIRouter]:
    """The ``router`` of every module in this package, sorted by module name."""
    routers = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda module: module.name):
        module = importlib.import_module(f"{__name__}.{info.name}")
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise TypeError(f"{module.__name__} must define `router = APIRouter(...)`")
        routers.append(router)
    return routers

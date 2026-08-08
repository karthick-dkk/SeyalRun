"""Directory-scan plugin discovery.

Each service calls ``discover_plugins("app.plugins.idp", IdentityProvider)``
at startup. Every ``.py`` module in that package is imported, and any class
defined in it that subclasses ``base_class`` (and isn't ``base_class``
itself) is instantiated and registered under its ``name`` attribute.

No dynamic ``pip install`` — plugins ship inside the image, keeping
multi-stage builds immutable and minimal.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TypeVar

T = TypeVar("T")


def discover_plugins(package_name: str, base_class: type[T]) -> dict[str, T]:
    registry: dict[str, T] = {}
    try:
        package = importlib.import_module(package_name)
    except ModuleNotFoundError:
        return registry

    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package_name}.{module_name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is base_class or not issubclass(obj, base_class):
                continue
            if obj.__module__ != module.__name__:
                continue
            instance = obj()
            plugin_name = getattr(instance, "name", module_name)
            registry[plugin_name] = instance

    return registry

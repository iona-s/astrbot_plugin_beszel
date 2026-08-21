"""AstrBot loader shim for the Beszel plugin."""

from .core.plugin import BeszelPlugin, bind_loader_module

bind_loader_module(__name__)
del bind_loader_module

__all__ = ["BeszelPlugin"]

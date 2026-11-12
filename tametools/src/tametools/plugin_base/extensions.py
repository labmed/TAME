"""What a plugin can add besides an analysis: commands for the command line, a screen for its results, export formats.

All are registered when the plugin's file is loaded, exactly like ``register_plugin``, and all are plain
declarations: the host decides where they appear (``tametools NAME ...`` for a command; the web workbench and
``run-plugin --view`` for a screen; ``tametools export --format NAME`` for a format), so a plugin needs no code
of its own in the CLI or the web.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..config import ci_get
from .base import PluginError, normalize_plugin_name


@dataclass(frozen=True)
class CommandSpec:
    name: str                               # as typed: "qc-action"
    run: Callable[[Any], int]               # run(args) -> exit code; args is the parsed argparse namespace
    configure: Callable[[Any], None] | None = None      # configure(parser): add the command's arguments
    help: str = ""
    description: str = ""
    epilog: str = ""


@dataclass(frozen=True)
class ViewSpec:
    name: str                               # the plugin whose results it shows
    render: Callable[[Any], str]            # render(dataset) -> one complete HTML document
    title: str = ""


@dataclass(frozen=True)
class ExporterSpec:
    name: str                               # the format, as given to --format: "markdown"
    write: Callable[[Any, Any], Any]        # write(dataset, path) -> the file(s) written, or None for just ``path``
    suffixes: tuple[str, ...] = ()          # output names ending like this mean the format: (".md",)
    description: str = ""


COMMAND_REGISTRY: dict[str, CommandSpec] = {}
VIEW_REGISTRY: dict[str, ViewSpec] = {}
EXPORTER_REGISTRY: dict[str, ExporterSpec] = {}


def register_command(name: str, *, help: str = "", description: str = "", epilog: str = "",
                     configure: Callable[[Any], None] | None = None):
    """Register ``run(args) -> int`` as the command ``tametools NAME``.

    ``configure(parser)`` adds its arguments to an ``argparse`` parser. A built-in command of the same
    name always wins, so choose a name of your own (``qc-action``, not ``export``).
    """
    command = str(name).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", command):
        raise PluginError(f"Command name must be lower-case words joined by hyphens: {name!r}")

    def decorator(run):
        if not callable(run):
            raise PluginError(f"Command {command}: @register_command goes on the function that runs it")
        COMMAND_REGISTRY[command] = CommandSpec(command, run, configure, str(help), str(description or help), str(epilog))
        return run

    return decorator


def register_view(name: str, *, title: str = ""):
    """Register ``render(dataset) -> html`` as the screen for results of the plugin ``name``.

    A result belongs to a plugin through ``META[ANALYSIS_RESULT].PLUGIN`` (``VIEW`` names another one).
    The HTML must be one complete document that refers to no outside file; hosts show it isolated from
    their own page, and text that comes from the data must be escaped by the plugin.
    """
    key = normalize_plugin_name(name)
    if not key:
        raise PluginError("register_view needs the name of the plugin whose results it shows")

    def decorator(render):
        if not callable(render):
            raise PluginError(f"View {key}: @register_view goes on the function that renders it")
        VIEW_REGISTRY[key] = ViewSpec(key, render, str(title or key))
        return render

    return decorator


def register_exporter(name: str, *, suffixes: tuple[str, ...] | list[str] = (), description: str = ""):
    """Register ``write(dataset, path)`` as the export format ``name`` (``tametools export FILE OUT --format NAME``).

    ``write`` creates ``path`` (its folder already exists) and returns what it wrote: nothing when that is only
    ``path``, else a path or a list of paths. ``suffixes`` lets the format be inferred from the output name
    (``report.md`` for ``(".md",)``). The formats that come with tametools, and their suffixes, always win.
    """
    key = str(name).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]*([_-][a-z0-9]+)*", key):
        raise PluginError(f"Export format name must be lower-case words joined by - or _: {name!r}")
    endings = tuple(str(suffix).strip().lower() for suffix in ([suffixes] if isinstance(suffixes, str) else suffixes))
    if any(not re.fullmatch(r"(\.[a-z0-9_-]+)+", ending) for ending in endings):
        raise PluginError(f"Export format {key}: suffixes look like '.md' or '.fhir.json', not {list(endings)}")

    def decorator(write):
        if not callable(write):
            raise PluginError(f"Export format {key}: @register_exporter goes on the function that writes the file")
        EXPORTER_REGISTRY[key] = ExporterSpec(key, write, endings, str(description))
        return write

    return decorator


def view_for(dataset: Any) -> ViewSpec | None:
    """The registered screen for this result, if its plugin has one (plugins must already be loaded)."""
    declared = ci_get(getattr(dataset, "meta", None) or {}, "ANALYSIS_RESULT", {})
    if not isinstance(declared, dict):
        return None
    name = ci_get(declared, "VIEW", None) or ci_get(declared, "PLUGIN", None)
    return VIEW_REGISTRY.get(normalize_plugin_name(name)) if isinstance(name, str) and name.strip() else None


def render_view(dataset: Any) -> tuple[str, str] | None:
    """``(title, html)`` of the result's screen, or None when its plugin registered none."""
    spec = view_for(dataset)
    if spec is None:
        return None
    html = spec.render(dataset)
    if not isinstance(html, str) or not html.strip():
        raise PluginError(f"The view of {spec.name} must return an HTML document as text")
    return spec.title, html

"""The names tametools itself reads at the top level of META, and the names it runs as work steps.

A plugin's settings live in a META section named after the plugin (``[MY_PLUGIN]``), in the same place as the
sections below. So that the two cannot be confused, a plugin may not take one of these names
(``register_plugin`` refuses it), and a section whose name is one letter off a name somebody reads is pointed
out by ``tametools validate``.

This module imports nothing from tametools: the plugin registry and the command line both read it.
The human description of each section is in docs/TAME_META_SECTIONS.md.
"""
from __future__ import annotations

from difflib import get_close_matches

# section -> who owns its content
#   "file"    describes the data (written by people or by init)
#   "work"    declares what to run (written by people)
#   "state"   written by the tool, never by hand
#   "result"  marks what a result is and where it came from (written by the tool or a plugin)
#   "web"     kept by the web workbench
CORE_SECTIONS: dict[str, str] = {
    "INFO": "file", "COLUMN": "file", "TAGS": "file", "TAG_DEFINITIONS": "file", "SETTINGS": "file", "FORMAT": "file",
    "MULTISHEET": "file", "CATEGORIES": "file", "DATETIME": "file", "SEX_NORMALIZATION": "file", "OBSERVATION": "file",
    "REQUIREMENTS": "file", "SURVEY": "file", "SOURCE": "file", "SOURCE_CATALOG": "file", "INTEGRATION_PROFILE": "file",
    "ANALYSIS_CONTRACT": "file",
    "WORKS": "work", "SUBWORKS": "work", "PIPELINES": "work", "ACTIONS": "work", "ACTION_PIPELINES": "work",
    "ANALYSIS_PLAN": "work", "ANALYSES": "work", "FUNCTIONS": "work", "PLUGINS": "work", "USE": "work", "SYNC": "work",
    "INIT_OPTIONS": "work", "INIT_REVIEW": "work", "USER_GUIDE": "work",
    "LOG": "state", "PROVENANCE": "state", "INTEGRITY": "state", "SYNC_STATE": "state", "SYNC_RECORDS": "state",
    "INIT": "state",
    "ANALYSIS_RESULT": "result", "WORKFLOW_RESULT": "result", "PLUGIN": "result", "VISUALIZATIONS": "result",
    "INTEGRATION_RESULT": "result", "SYNC_RESULT": "result",
    "WEB_PLUGINS": "web", "WEB_WORKBENCH": "web", "NHANES_WORKFLOW": "web",
}

# What a WORKS entry of this name runs is a step of tametools itself; a plugin of the same name would never be reached.
# (tests keep this equal to the step handlers in tametools.pipeline)
STEP_NAMES: frozenset[str] = frozenset({
    "VALIDATE", "DESCRIBE", "EDA", "CENSOR", "SURVEY_DESCRIBE", "ANALYZE", "CLINICAL_ANALYSIS", "EVALUATE", "ANONYMIZE",
    "SAMPLE", "SPLIT_COMPARATOR", "SPLIT_CNUM", "HARMONIZE_COMPARATOR", "HARMONIZE_CNUM", "INTEGRATE", "COLUMNS", "INFO",
})

RESERVED_NAMES: frozenset[str] = frozenset(CORE_SECTIONS) | STEP_NAMES


def is_reserved(name: str) -> bool:
    return str(name).strip().upper().replace("-", "_") in RESERVED_NAMES


def step_sections(meta: dict | None) -> set[str]:
    """Top-level sections that are the settings of a declared step: named in WORKS/SUBWORKS, or naming a PLUGIN/FUNCTION."""
    if not isinstance(meta, dict):
        return set()
    upper = {str(key).upper(): value for key, value in meta.items()}
    names: set[str] = set()
    for section in ("WORKS", "SUBWORKS"):
        declared = upper.get(section)
        if isinstance(declared, dict):
            for steps in declared.values():
                if isinstance(steps, list):
                    names.update(str(step).upper() for step in steps)
    for key, value in upper.items():
        if isinstance(value, dict) and any(str(item).upper() in {"PLUGIN", "FUNCTION"} for item in value):
            names.add(key)
    return names


def section_typos(meta: dict | None, plugin_names=()) -> list[str]:
    """Sections nobody reads whose name is almost a name somebody does read (``[WESTGARD_QCC]`` for ``[WESTGARD_QC]``).

    Only near misses are reported. A file may carry sections of its own; those are left alone.
    """
    if not isinstance(meta, dict):
        return []
    known = set(RESERVED_NAMES) | {str(name).strip().upper().replace("-", "_") for name in plugin_names} | step_sections(meta)
    notes = []
    for key in meta:
        name = str(key).upper()
        if name in known or not isinstance(meta[key], dict):
            continue
        close = get_close_matches(name, sorted(known), n=1, cutoff=0.86)
        if close:
            notes.append(f"META[{key}] is not read by tametools or by an available plugin. Did you mean [{close[0]}]? "
                         "Its settings are not applied as written.")
    return notes

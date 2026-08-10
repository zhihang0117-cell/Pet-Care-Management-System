"""
Deterministic text-formatting helpers.

Ported from the earlier PAWFECT_LLM prototype's greeting_composer.py.
Only the pure, non-behavioral formatting functions are kept here — the
prototype's full compose_greeting() (a template that replaced the LLM's
own greeting entirely) is intentionally NOT ported, since that would
duplicate/compete with SYSTEM_PROMPT rule 12 (SMART GREETING), which
already owns greeting behavior in this codebase.

These helpers exist so any part of the app (currently: orchestrator.py's
identity resolution) can hand the model an already-consistent, tested
string instead of asking the model to format a list of names itself
every turn.
"""
from __future__ import annotations

from typing import Any


def first_name(full_name: str | None) -> str:
    """'Jane Doe' -> 'Jane'. Empty/None -> ''."""
    if not full_name:
        return ""
    return full_name.strip().split()[0]


def format_name_list(names: list[str]) -> str:
    """
    Join names the way a person would say them out loud.

    ["Milo"] -> "Milo"
    ["Milo", "Luna"] -> "Milo or Luna"
    ["Milo", "Luna", "Coco"] -> "Milo, Luna, or Coco"
    """
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return ", ".join(names[:-1]) + f", or {names[-1]}"


def format_pet_names(pets: list[dict[str, Any]]) -> str:
    """Convenience wrapper: extract pet_name from a list of pet dicts, then format_name_list()."""
    names = [str(p.get("pet_name") or "").strip() for p in pets]
    return format_name_list([n for n in names if n])

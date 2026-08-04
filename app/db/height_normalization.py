"""Normalize a customer-stated pet height into centimeters.

The system always asks "how tall, in cm" (see system_prompt.py), but
customers naturally answer in whatever unit they think in — inches, feet,
or meters, in English or Chinese. Previously the create_pet tool took
height_cm as an already-converted number with no deterministic backstop:
correctness depended entirely on the model doing unit math in its head with
no verification, the same class of problem date/time parsing had before
this module's siblings (date_normalization.py, time_normalization.py) were
generalized.
"""

from __future__ import annotations

import re

_CM_WORDS = r"cm|centimeters?|centimetres?|厘米|公分"
_INCH_WORDS = 'in|inch(?:es)?|″|"|英寸|寸'
_FEET_WORDS = r"ft|feet|foot|′|'"
_METER_WORDS = r"m|meters?|metres?|米"

_INCHES_PER_FOOT = 12
_CM_PER_INCH = 2.54


def parse_height_cm(value: str | None) -> float | None:
    """
    Parse a customer's stated pet height into centimeters.

    Accepts an explicit unit (cm/inch/feet/m, English or Chinese) and
    converts it; a bare number with no unit is assumed to already be cm,
    matching this system's existing convention. Returns None if no number
    can be found at all.

    The trailing (?![a-zA-Z]) after each unit alternative matters: without
    it "30 minutes" would match the single-letter "m" (meter) prefix of
    "minutes" and silently misread as 3000cm. With it, that match is
    rejected and the number still resolves via the safe bare-number-means-cm
    fallback. A plain \\b doesn't work here instead: it correctly blocks the
    "m"-of-"minutes" case but also blocks the symbol units ("24\"", "2'") at
    end-of-string, since a symbol-to-nothing transition isn't a \\b boundary.
    """
    text = str(value or "").strip().lower()
    if not text:
        return None

    combo = re.search(
        rf"(\d+(?:\.\d+)?)\s*(?:{_FEET_WORDS})(?![a-zA-Z])\s*(\d+(?:\.\d+)?)\s*(?:{_INCH_WORDS})(?![a-zA-Z])",
        text,
    )
    if combo:
        feet, inches = float(combo.group(1)), float(combo.group(2))
        return round((feet * _INCHES_PER_FOOT + inches) * _CM_PER_INCH, 1)

    match = re.search(
        rf"(\d+(?:\.\d+)?)\s*(?:({_CM_WORDS})|({_INCH_WORDS})|({_FEET_WORDS})|({_METER_WORDS}))?(?![a-zA-Z])",
        text,
    )
    if not match:
        return None
    number = float(match.group(1))
    if match.group(3):  # inch
        return round(number * _CM_PER_INCH, 1)
    if match.group(4):  # feet
        return round(number * _INCHES_PER_FOOT * _CM_PER_INCH, 1)
    if match.group(5):  # meter
        return round(number * 100, 1)
    # explicit cm, or no unit at all — both are already centimeters
    return round(number, 1)

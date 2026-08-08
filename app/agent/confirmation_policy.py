"""Fail-closed natural-language confirmation classification."""

from __future__ import annotations

import re


AFFIRMATIVE_RE = re.compile(
    r"^(?:yes|y|correct|confirm(?:ed)?|proceed|continue|go\s+ahead|book\s+it|"
    r"do\s+it|ok(?:ay)?|sure|ya|boleh|ya\s+boleh|teruskan|sahkan|可以|确认|"
    r"確認|正确|正確|对|對|没错|沒錯|是的|好|好的|同意|继续|繼續|没问题|"
    r"沒問題)$",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
    r"^(?:no(?:\s*no)?|n|none|cancel|stop|don't|do\s+not|tak|tidak|jangan|"
    r"不要|取消|不用|不确认|不確認)$",
    re.IGNORECASE,
)


def confirmation_intent(user_message: str) -> str | None:
    """Classify only a standalone affirmative/negative authorization.

    Politeness and repeated affirmative atoms are accepted, while any business
    detail left after normalization makes the message non-standalone.
    """
    compact = re.sub(
        r"[\s.!?,，。！？]+", " ", str(user_message or "").strip()
    ).strip()
    compact = re.sub(
        r"\b(?:please|kindly)\b|请|請|麻烦|麻煩",
        " ",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(r"\s+", " ", compact).strip()
    if AFFIRMATIVE_RE.fullmatch(compact):
        return "affirmative"

    affirmative_atom = (
        r"(?:yes|y|correct|confirm(?:ed)?|proceed|continue|go\s+ahead|"
        r"book\s+it|do\s+it|ok(?:ay)?|sure|ya|boleh|teruskan|sahkan)"
    )
    if re.fullmatch(
        rf"{affirmative_atom}(?:\s+{affirmative_atom})+",
        compact,
        re.IGNORECASE,
    ):
        return "affirmative"

    cjk_compact = re.sub(r"\s+", "", compact)
    if re.fullmatch(
        r"(?:(?:可以|确认|確認|正确|正確|对|對|没错|沒錯|是的|好|好的|"
        r"同意|继续|繼續|没问题|沒問題)){1,4}(?:预约|預約)?",
        cjk_compact,
    ):
        return "affirmative"

    compact = re.sub(
        r"\s+(?:thanks?|thank\s+you)$", "", compact, flags=re.IGNORECASE
    ).strip()
    if NEGATIVE_RE.fullmatch(compact):
        return "negative"
    return None

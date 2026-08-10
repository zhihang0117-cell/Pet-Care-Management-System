"""Regression coverage for app/agent/confirmation.py's standalone
affirmative/negative classification.

Moved from tests/test_constrained_ai_backend.py during the 2026-08-10 V1
decommission — confirmation_intent itself was extracted out of the
now-deleted app/agent/guardrails.py (see app/agent/confirmation.py's
docstring) since app/agent/policy.py (V2's authorize_confirm) still needs
it; this coverage moved with it rather than disappearing along with the
rest of guardrails.py.
"""

from app.agent.confirmation import confirmation_intent


def test_natural_standalone_confirmation_phrases_are_accepted():
    accepted = [
        "yes go ahead",
        "sure, book it",
        "correct please",
        "yes please book it",
        "可以，继续",
        "好的，请继续",
    ]
    for phrase in accepted:
        assert confirmation_intent(phrase) == "affirmative"

    assert confirmation_intent("yes, but change it to 3pm") is None
    assert confirmation_intent("no thanks") == "negative"


def test_elongated_affirmative_texting_habits_are_still_accepted():
    """Real gap confirmed 2026-08-10: "yessss"/"confirmmmm" (a completely
    normal WhatsApp emphasis habit) didn't match the exact "yes"/"confirm"
    the old regex required, silently falling through to a full re-ask."""
    for phrase in ("yesss", "yessss", "confirmm", "confirmmmm", "okkkk", "sureee"):
        assert confirmation_intent(phrase) == "affirmative", phrase


def test_confirmation_with_extra_request_is_not_standalone():
    assert confirmation_intent("yes please") == "affirmative"
    assert confirmation_intent("yes, please") == "affirmative"
    assert confirmation_intent("yes please, and book grooming") is None

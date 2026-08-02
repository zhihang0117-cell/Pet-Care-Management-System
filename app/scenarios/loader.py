import json
from pathlib import Path

DIR = Path(__file__).resolve().parent

def load_scenario(name: str) -> dict:
    path = DIR / f"{name.lower()}.json"
    if not path.exists():
        raise ValueError(f"Unknown scenario: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def valid_steps(scenario_name: str, service_type: str | None = None) -> set[str] | None:
    """
    Real current_step vocabulary for a scenario, from its JSON definition —
    the source of truth `update_conversation_state` checks against instead
    of accepting any string. Returns None when the scenario has no step
    list at all (e.g. POLICY_QUERY) or is unknown, meaning: don't validate.

    This intentionally does not enforce step ORDER or block tool calls —
    only that a claimed current_step is a real, known one for this
    scenario/service_type, so a typo or drifted value (e.g. a DAYCARE step
    name leaking into a GROOMING conversation) never silently persists.
    """
    try:
        data = load_scenario(scenario_name)
    except ValueError:
        return None
    vocabulary = data.get("step_vocabulary")
    if isinstance(vocabulary, dict):
        flows = vocabulary
        service = str(service_type or "").strip().upper()
        if service in flows:
            return set(flows[service])
        # service_type not resolved yet — permissive union across all
        # services rather than rejecting a step we can't yet disambiguate.
        return set().union(*flows.values())
    if isinstance(vocabulary, list):
        return set(vocabulary)
    return None

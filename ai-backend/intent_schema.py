"""
Shared intent JSON schema constants and normalization for Pawfect backend.
"""

import re

from booking_draft import is_explicit_booking_request

POLICY_RAG_SCENARIOS = {
    "CANCELLATION_POLICY",
    "GROOMING_POLICY",
    "BOARDING_POLICY",
    "DAYCARE_POLICY",
    "VET_REQUIREMENT",
    "SERVICE_INFORMATION",
    "LOYALTY_POLICY",
    "GENERAL_POLICY",
}

BOOKING_CHANGE_SCENARIOS = {
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
}

DATABASE_SCENARIOS = {
    "VIEW_BOOKING_STATUS",
    "CHECK_AVAILABILITY",
    "CHECK_LOYALTY_POINTS",
    "REDEEM_REWARD",
    "CHECK_MEMBERSHIP_STATUS",
    "LOYALTY_ACCOUNT_INQUIRY",
    "CHECK_COUPON_ELIGIBILITY",
    "CUSTOMER_GREETING",
    "REPEAT_LAST_BOOKING",
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
    "CONFIRM_BOOKING",
    "CREATE_CUSTOMER",
    "CREATE_PET",
    "VIEW_PAYMENT_HISTORY",
    "VIEW_REDEMPTION_HISTORY",
    "VIEW_MESSAGE_HISTORY",
    "VIEW_COMPANY_INFORMATION",
    "VIEW_STAFF_DIRECTORY",
    "VIEW_ACCOUNT_STATUS",
}

# Legacy labels kept for mock fallback compatibility
LEGACY_DATABASE_SCENARIOS = {
    "CHECK_POINTS_BALANCE": "CHECK_LOYALTY_POINTS",
    "POINTS_BALANCE": "CHECK_LOYALTY_POINTS",
    "REDEEM_POINTS": "REDEEM_REWARD",
    "BOOKING_STATUS": "VIEW_BOOKING_STATUS",
    "CHECK_BOOKING_STATUS": "VIEW_BOOKING_STATUS",
}

RETRIEVAL_SOURCE_BY_SCENARIO = {
    "CANCELLATION_POLICY": ["cancellation_policy"],
    "GROOMING_POLICY": ["grooming_policy"],
    "BOARDING_POLICY": ["boarding_policy"],
    "DAYCARE_POLICY": ["daycare_policy"],
    "VET_REQUIREMENT": ["vet_requirement_policy"],
    "SERVICE_INFORMATION": ["service_information"],
    "LOYALTY_POLICY": ["loyalty_policy"],
    "GENERAL_POLICY": ["general_policy"],
}

ENTITY_DEFAULTS = {
    "customer_name": "",
    "phone_number": "",
    "customer_identifier": "",
    "booking_id": "",
    "service_type": "",
    "preferred_date": "",
    "preferred_time": "",
    "new_preferred_date": "",
    "new_preferred_time": "",
    "pet_name": "",
    "pet_type": "",
    "pet_size": "",
    "pet_height": "",
    "vaccination_status": "",
    "check_in_date": "",
    "check_out_date": "",
    "policy_type": "",
    "reward_type": "",
}

ALLOWED_MAIN_INTENTS = {
    "BOOKING_INTENT",
    "POLICY_INTENT",
    "LOYALTY_INTENT",
    "GREETING_INTENT",
    "ACCOUNT_INTENT",
    "UNKNOWN",
}

ALLOWED_SCENARIO_INTENTS = {
    "MAKE_BOOKING",
    "COLLECT_CUSTOMER_NAME",
    "CANCEL_BOOKING",
    "RESCHEDULE_BOOKING",
    "VIEW_BOOKING_STATUS",
    "CHECK_AVAILABILITY",
    "REPEAT_LAST_BOOKING",
    "CONFIRM_BOOKING",
    "CREATE_CUSTOMER",
    "CREATE_PET",
    *POLICY_RAG_SCENARIOS,
    *DATABASE_SCENARIOS,
    "UNKNOWN",
}

ALLOWED_SERVICE_TYPES = {"GROOMING", "DAYCARE", "BOARDING", "GENERAL", "UNKNOWN"}
ALLOWED_PET_TYPES = {"DOG", "CAT", "ALL", "UNKNOWN"}
ALLOWED_PET_SIZES = {"XS", "S", "M", "L", "XL", "XXL", "UNKNOWN"}
ALLOWED_CUSTOMER_STATUS = {"NEW_CUSTOMER", "EXISTING_CUSTOMER", "UNKNOWN"}

# General loyalty-program rules — not personal account lookups.
_LOYALTY_POLICY_SIGNAL = re.compile(
    r"\b("
    r"how\s+(do|does|to|can|are|is|many)|"
    r"earn(ing)?|expir(e|y|ation)|valid(ity)?|"
    r"redeem(tion)?(\s+(rule|policy|work|free|a|the))?|"
    r"program(me)?\s+(work|rule)|"
    r"loyalty\s+(rule|benefit|term)|"
    r"membership\s+rule|reward\s+(rule|term|policy)|"
    r"points?\s+(valid|expir|earn|to\s+redeem|needed|required)"
    r")\b",
    re.I,
)

# Customer-specific loyalty account status or balance.
_LOYALTY_ACCOUNT_SIGNAL = re.compile(
    r"(^|\b)("
    r"how\s+many\s+(loyalty\s+)?points|"
    r"(my\s+)?(loyalty\s+)?(points?|pts|point\s*balance|rewards?\s*balance)|"
    r"(my\s+)?(points?\s*balance|balance)|"
    r"check\s+my\s+(loyalty\s+)?(points?|rewards?|membership)|"
    r"do\s+i\s+have\s+enough\s+points|"
    r"(my\s+)?(tier|member\s*level|membership\s*level|membership\s*status|loyalty\s*status)|"
    r"am\s+i\s+(a\s+)?(silver|gold|platinum|bronze)\s+member|"
    r"what\s+member\s+am\s+i|"
    r"check\s+my\s+membership|my\s+loyalty"
    r")(\?|\b)",
    re.I,
)

_LOYALTY_MEMBERSHIP_STATUS_SIGNAL = re.compile(
    r"(^|\b)(my\s+)?(tier|member\s*level|membership\s*level|membership\s*status|"
    r"loyalty\s*status|am\s+i\s+(a\s+)?(silver|gold|platinum|bronze)\s+member|"
    r"what\s+member\s+am\s+i|check\s+my\s+membership)(\?|\b)",
    re.I,
)

_COUPON_ELIGIBILITY_SIGNAL = re.compile(
    r"\b(?:coupon|coupons|voucher|vouchers|reward|rewards)\b"
    r".{0,55}\b(?:redeem|exchange|enough|eligible|afford|points?)\b|"
    r"\b(?:redeem|exchange|enough|eligible|afford|points?)\b"
    r".{0,55}\b(?:coupon|coupons|voucher|vouchers|reward|rewards)\b",
    re.I,
)

# Live slot/space/room availability on a date — not service descriptions.
_AVAILABILITY_NON_SLOT_TIME = re.compile(
    r"\b(nap|meal|play|rest|quiet|feeding|sleep)\s+time\b",
    re.I,
)

_AVAILABILITY_SIGNAL = re.compile(
    r"\b("
    r"(got|any|have|is there|are there)\s+(a\s+)?(slot|room|space|place|spot|capacity)|"
    r"(slot|room|space|place|spot)\s+(available|got|free|open)|"
    r"(boarding|grooming|daycare|bath)\s+(available|slot|space|room)|"
    r"available\s+(for|on|next|this)|"
    r"can\s+fit\s+(my|the|a)\s+(dog|cat|pet)|"
    r"can\s+(i\s+)?book\s+(a\s+)?(slot|room|space|place)?"
    r")\b|"
    r"\b(got|any|have|available)\b.{0,50}\b("
    r"tomorrow|today|next\s+week|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|\d{4}-\d{2}-\d{2}|this\s+(week|friday|monday)"
    r")\b|"
    r"\b("
    r"tomorrow|today|next\s+week|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|\d{4}-\d{2}-\d{2}|this\s+(week|friday|monday)"
    r")\b.{0,50}\b(available|slot|room|space|place|spot)\b",
    re.I,
)


# Pure greeting openers — must not match messages with additional requests.
_GREETING_ONLY = re.compile(
    r"^\s*(?:"
    r"hi+|hello+|hey+|heya|hiya|"
    r"good\s+(?:morning|afternoon|evening|day)|"
    r"(?:good\s+)?morning|(?:good\s+)?evening|"
    r"yo|sup|"
    r"(?:hi+|hello+)\s+there"
    r")\s*[!.?😊🙂👋]*\s*$",
    re.I,
)

_GREETING_PREFIX = re.compile(
    r"^\s*(?:"
    r"hi+|hello+|hey+|heya|hiya|"
    r"good\s+(?:morning|afternoon|evening|day)|"
    r"(?:good\s+)?morning|(?:good\s+)?evening|"
    r"yo|sup|"
    r"(?:hi+|hello+)\s+there"
    r")\s*[,!.]?\s+(.+)$",
    re.I,
)


def _normalize_message_text(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "").strip().lower())


_BOOKING_STATUS_SIGNAL = re.compile(
    r"\b("
    r"(what\s+is|check|what'?s|can\s+i\s+check)\s+(my\s+)?(booking|appointment)(\s+status|\s+details)?|"
    r"(my\s+)?(appt|appointment|booking)\b.*\b(when|status|time|date|confirmed)\b|"
    r"when\s+is\s+my\s+(appt|appointment|booking)|"
    r"my\s+(appt|appointment|booking)\s+when|"
    r"is\s+my\s+(appt|appointment|booking)\s+confirmed|"
    r"what\s+time\s+is\s+my\s+(booking|appointment)|"
    r"do\s+i\s+have\s+(any\s+)?(upcoming\s+)?(bookings?|appointments?)|"
    r"upcoming\s+(bookings?|appointments?)|"
    r"check\s+.+\s+(grooming|daycare|boarding)\s+(appt|appointment|booking)|"
    r"check\s+.+\s+(appt|appointment)"
    r")\b",
    re.I,
)


def _is_booking_status_query(message: str) -> bool:
    text = _normalize_message_text(message)
    return bool(_BOOKING_STATUS_SIGNAL.search(text))


def has_greeting_prefix(message: str) -> bool:
    """True when message starts with a greeting but includes additional content."""
    text = str(message or "").strip()
    if not text or _is_greeting_message(text):
        return False
    return bool(_GREETING_PREFIX.match(text))


def strip_greeting_prefix(message: str) -> tuple[bool, str]:
    """Return (has_prefix, remainder) for greeting+request messages."""
    text = str(message or "").strip()
    match = _GREETING_PREFIX.match(text)
    if not match:
        return False, text
    remainder = str(match.group(1) or "").strip()
    return True, remainder


def _is_greeting_message(message: str) -> bool:
    """True when the message is only a greeting with no additional request."""
    text = _normalize_message_text(message)
    if not text:
        return False
    return bool(_GREETING_ONLY.match(text))


def _is_repeat_last_booking_query(message: str) -> bool:
    text = _normalize_message_text(message)
    if not text:
        return False
    patterns = (
        r"same\s+service\s+as\s+last\s+time",
        r"same\s+as\s+last\s+time",
        r"repeat\s+my\s+last\s+booking",
        r"book\s+the\s+same\s+service\s+again",
        r"can\s+do\s+like\s+last\s+time",
        r"same\s+one\s+as\s+before",
        r"same\s+(grooming|daycare|boarding)\s+as\s+before",
        r"like\s+last\s+time",
    )
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _is_loyalty_account_query(message: str) -> bool:
    text = _normalize_message_text(message)
    if not text:
        return False
    if _LOYALTY_ACCOUNT_SIGNAL.search(text):
        return True
    return False


def _loyalty_scenario_for_message(message: str) -> str:
    text = _normalize_message_text(message)
    if _LOYALTY_MEMBERSHIP_STATUS_SIGNAL.search(text):
        return "CHECK_MEMBERSHIP_STATUS"
    return "CHECK_LOYALTY_POINTS"


def _is_availability_query(message: str) -> bool:
    text = _normalize_message_text(message)
    if not text:
        return False
    if _AVAILABILITY_NON_SLOT_TIME.search(text):
        return False
    if _AVAILABILITY_SIGNAL.search(text):
        return True
    return bool(
        re.search(
            r"\b(got|any|have|can)\b.{0,40}\b(slot|room|space|place|spot)\b",
            text,
            re.I,
        )
    )


def apply_message_pattern_overrides(user_message: str, intent: dict) -> dict:
    """
    Pattern-level routing corrections after LLM classification.

    Covers semantic families the prompt may still mislabel (loyalty account vs
    policy, live availability vs service information) without hardcoding eval rows.
    """
    if not isinstance(intent, dict):
        return intent

    result = dict(intent)
    scenario = result.get("scenario_intent", "UNKNOWN")
    main = result.get("main_intent", "UNKNOWN")

    effective_message = user_message
    greeting_prefix = False
    if has_greeting_prefix(user_message):
        greeting_prefix, effective_message = strip_greeting_prefix(user_message)
        result["greeting_prefix_detected"] = True

    if _is_greeting_message(user_message):
        result["main_intent"] = "GREETING_INTENT"
        result["scenario_intent"] = "CUSTOMER_GREETING"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_customer_by_phone"
        result["next_action"] = "check_customer_by_phone"
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.95)
        result["missing_information"] = []
        return result

    if greeting_prefix and not str(effective_message or "").strip():
        result["main_intent"] = "GREETING_INTENT"
        result["scenario_intent"] = "CUSTOMER_GREETING"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_customer_by_phone"
        result["next_action"] = "check_customer_by_phone"
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.95)
        result["missing_information"] = []
        return result

    relational_read_patterns = (
        (
            r"\b(my\s+)?(payment|payments|receipt|receipts|payment\s+history|paid)\b",
            "VIEW_PAYMENT_HISTORY",
            "get_payment_history",
        ),
        (
            r"\b(my\s+)?(redemption|redemptions|redeemed\s+rewards?|reward\s+history)\b",
            "VIEW_REDEMPTION_HISTORY",
            "get_redemption_history",
        ),
        (
            r"\b(my\s+)?(message|messages|chat|conversation)\s+history\b",
            "VIEW_MESSAGE_HISTORY",
            "get_message_history",
        ),
        (
            r"\b(company|business)\s+(information|details|address|location)\b",
            "VIEW_COMPANY_INFORMATION",
            "get_company_information",
        ),
        (
            r"\b(staff|team|groomers?)\b",
            "VIEW_STAFF_DIRECTORY",
            "get_staff_directory",
        ),
        (
            r"\b(my\s+)?(account|profile)\s+(status|details|information)\b",
            "VIEW_ACCOUNT_STATUS",
            "get_customer_profile",
        ),
    )
    for pattern, relational_scenario, action in relational_read_patterns:
        if not re.search(pattern, effective_message, re.I):
            continue
        result["main_intent"] = "ACCOUNT_INTENT"
        result["scenario_intent"] = relational_scenario
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = action
        result["next_action"] = action
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.95)
        result["missing_information"] = []
        return result

    if _is_repeat_last_booking_query(effective_message):
        result["main_intent"] = "BOOKING_INTENT"
        result["scenario_intent"] = "REPEAT_LAST_BOOKING"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_last_booking"
        result["next_action"] = "check_last_booking"
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.95)
        result["missing_information"] = []
        return result

    if _is_booking_status_query(effective_message):
        result["main_intent"] = "BOOKING_INTENT"
        result["scenario_intent"] = "VIEW_BOOKING_STATUS"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_booking_status"
        result["next_action"] = "check_booking_status"
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.92)
        return result

    if _COUPON_ELIGIBILITY_SIGNAL.search(effective_message):
        with_booking = is_explicit_booking_request(effective_message)
        result["main_intent"] = "LOYALTY_INTENT"
        result["scenario_intent"] = "CHECK_COUPON_ELIGIBILITY"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_coupon_eligibility"
        result["next_action"] = "check_coupon_eligibility"
        result["coupon_eligibility_with_booking"] = with_booking
        result["missing_information"] = []
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.97)
        return result

    if re.search(r"\buse\s+\d+\s+points?\b", effective_message, re.I):
        points_match = re.search(r"\buse\s+(\d+)\s+points?\b", effective_message, re.I)
        entities = dict(result.get("entities") or {})
        if points_match:
            entities["points_to_redeem"] = points_match.group(1)
        result["entities"] = entities
        result["main_intent"] = "LOYALTY_INTENT"
        result["scenario_intent"] = "REDEEM_REWARD"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "redeem_reward"
        result["next_action"] = "redeem_reward"
        return result

    if re.search(r"\b(move|reschedule)\b.*\bappointment\b", effective_message, re.I):
        entities = dict(result.get("entities") or {})
        from session_continuation import _extract_preferred_date, _extract_preferred_time

        new_date = _extract_preferred_date(effective_message)
        new_time = _extract_preferred_time(effective_message)
        if new_date:
            entities["new_preferred_date"] = new_date
        if new_time:
            entities["new_preferred_time"] = new_time
        result["entities"] = entities
        result["main_intent"] = "BOOKING_INTENT"
        result["scenario_intent"] = "RESCHEDULE_BOOKING"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "reschedule_booking"
        result["next_action"] = "reschedule_booking"
        return result

    if re.search(r"\bcancel\s+my\s+booking\b", effective_message, re.I) or (
        "cancel" in _normalize_message_text(effective_message)
        and "policy" not in _normalize_message_text(effective_message)
        and "booking" in _normalize_message_text(effective_message)
    ):
        result["main_intent"] = "BOOKING_INTENT"
        result["scenario_intent"] = "CANCEL_BOOKING"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "cancel_booking"
        result["next_action"] = "cancel_booking"
        return result

    if is_explicit_booking_request(effective_message) and not _is_availability_query(effective_message):
        result["main_intent"] = "BOOKING_INTENT"
        result["scenario_intent"] = "MAKE_BOOKING"
        result["database_action_needed"] = False
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = ""
        result["next_action"] = "ask_missing_information"
        result["confidence"] = max(float(result.get("confidence") or 0.0), 0.92)
        return result

    if _is_loyalty_account_query(effective_message):
        loyalty_scenario = _loyalty_scenario_for_message(effective_message)
        result["main_intent"] = "LOYALTY_INTENT"
        result["scenario_intent"] = loyalty_scenario
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_loyalty_points"
        result["next_action"] = (
            "check_membership_status"
            if loyalty_scenario == "CHECK_MEMBERSHIP_STATUS"
            else "check_loyalty_points"
        )
        return result

    if _is_availability_query(effective_message) and scenario in {
        "SERVICE_INFORMATION",
        "GROOMING_POLICY",
        "BOARDING_POLICY",
        "DAYCARE_POLICY",
        "GENERAL_POLICY",
        "MAKE_BOOKING",
        "UNKNOWN",
    }:
        result["main_intent"] = "BOOKING_INTENT"
        result["scenario_intent"] = "CHECK_AVAILABILITY"
        result["database_action_needed"] = True
        result["retrieval_needed"] = False
        result["retrieval_source"] = []
        result["database_action"] = "check_availability"
        result["next_action"] = "check_availability"
        from session_continuation import _message_has_time_hint

        if _message_has_time_hint(effective_message):
            result["user_action"] = "PROVIDE_TIME_AND_CHECK_AVAILABILITY"
        return result

    if scenario == "CHECK_AVAILABILITY" and main != "BOOKING_INTENT":
        result["main_intent"] = "BOOKING_INTENT"

    if scenario in {"SERVICE_INFORMATION", "GENERAL_POLICY", "LOYALTY_POLICY", "GROOMING_POLICY"}:
        if _is_booking_status_query(effective_message):
            result["main_intent"] = "BOOKING_INTENT"
            result["scenario_intent"] = "VIEW_BOOKING_STATUS"
            result["database_action_needed"] = True
            result["retrieval_needed"] = False
            result["retrieval_source"] = []
            result["database_action"] = "check_booking_status"
            result["next_action"] = "check_booking_status"
            result["confidence"] = max(float(result.get("confidence") or 0.0), 0.92)
            result["missing_information"] = []
            return result
        if _is_loyalty_account_query(effective_message):
            loyalty_scenario = _loyalty_scenario_for_message(effective_message)
            result["main_intent"] = "LOYALTY_INTENT"
            result["scenario_intent"] = loyalty_scenario
            result["database_action_needed"] = True
            result["retrieval_needed"] = False
            result["retrieval_source"] = []
            result["database_action"] = "check_loyalty_points"
            result["next_action"] = (
                "check_membership_status"
                if loyalty_scenario == "CHECK_MEMBERSHIP_STATUS"
                else "check_loyalty_points"
            )
            result["confidence"] = max(float(result.get("confidence") or 0.0), 0.92)
            result["missing_information"] = []
            return result

    return result


def normalize_scenario_intent(scenario_intent: str) -> str:
    """Map legacy scenario labels to the current prompt labels."""
    if scenario_intent in LEGACY_DATABASE_SCENARIOS:
        return LEGACY_DATABASE_SCENARIOS[scenario_intent]
    return scenario_intent


def normalize_entities(raw_entities: dict | None) -> dict:
    """Normalize entity extraction output to the expected schema."""
    entities = dict(ENTITY_DEFAULTS)
    if isinstance(raw_entities, dict):
        for key in ENTITY_DEFAULTS:
            value = raw_entities.get(key, "")
            entities[key] = "" if value is None else str(value)
    return entities


def normalize_intent_result(raw_result: dict) -> dict:
    """Validate and normalize LLM JSON to the expected schema."""
    main_intent = raw_result.get("main_intent", "UNKNOWN")
    scenario_intent = normalize_scenario_intent(raw_result.get("scenario_intent", "UNKNOWN"))
    service_type = raw_result.get("service_type", "UNKNOWN")
    pet_type = raw_result.get("pet_type", "UNKNOWN")
    pet_size = raw_result.get("pet_size", "UNKNOWN")
    pet_height = raw_result.get("pet_height", "")
    customer_status = raw_result.get("customer_status", "UNKNOWN")
    entities = normalize_entities(raw_result.get("entities"))
    missing_information = raw_result.get("missing_information", [])
    retrieval_needed = raw_result.get("retrieval_needed", False)
    retrieval_source = raw_result.get("retrieval_source", [])
    database_action_needed = raw_result.get("database_action_needed", False)
    database_action = raw_result.get("database_action", "")
    next_action = raw_result.get("next_action", "clarify_request")
    reason = raw_result.get("reason", "")
    handoff_reason = str(raw_result.get("handoff_reason") or "").strip()
    confidence = raw_result.get("confidence", 0.40)

    if main_intent not in ALLOWED_MAIN_INTENTS:
        main_intent = "UNKNOWN"
    if scenario_intent not in ALLOWED_SCENARIO_INTENTS:
        scenario_intent = "UNKNOWN"
    if service_type not in ALLOWED_SERVICE_TYPES:
        service_type = "UNKNOWN"
    if pet_type not in ALLOWED_PET_TYPES:
        pet_type = "UNKNOWN"
    if pet_size not in ALLOWED_PET_SIZES:
        pet_size = "UNKNOWN"
    if customer_status not in ALLOWED_CUSTOMER_STATUS:
        customer_status = "UNKNOWN"
    if not isinstance(missing_information, list):
        missing_information = []
    if not isinstance(retrieval_source, list):
        retrieval_source = []
    if not isinstance(next_action, str):
        next_action = "clarify_request"
    if not isinstance(database_action, str):
        database_action = ""
    if not isinstance(reason, str):
        reason = ""
    if pet_height is None:
        pet_height = ""
    else:
        pet_height = str(pet_height)

    retrieval_needed = bool(retrieval_needed)
    database_action_needed = bool(database_action_needed)

    if scenario_intent in POLICY_RAG_SCENARIOS and not retrieval_source:
        retrieval_source = list(RETRIEVAL_SOURCE_BY_SCENARIO.get(scenario_intent, []))

    if scenario_intent in POLICY_RAG_SCENARIOS:
        retrieval_needed = True
        database_action_needed = False
        retrieval_source = list(RETRIEVAL_SOURCE_BY_SCENARIO.get(scenario_intent, retrieval_source))
        database_action = "retrieve_service_info" if scenario_intent == "SERVICE_INFORMATION" else "retrieve_policy"
        if next_action in ("", "clarify_request", "check_loyalty_points", "check_booking_status", "check_availability"):
            next_action = database_action

    if scenario_intent == "CANCEL_BOOKING":
        main_intent = "BOOKING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "cancel_booking"
        next_action = "cancel_booking"

    if scenario_intent == "RESCHEDULE_BOOKING":
        main_intent = "BOOKING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "reschedule_booking"
        next_action = "reschedule_booking"

    if scenario_intent == "REDEEM_REWARD":
        main_intent = "LOYALTY_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "redeem_reward"
        next_action = "redeem_reward"

    if scenario_intent == "CONFIRM_BOOKING":
        main_intent = "BOOKING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "create_booking"
        next_action = "create_booking"
        missing_information = []

    if scenario_intent == "CREATE_CUSTOMER":
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "create_customer"
        next_action = "create_customer"

    if scenario_intent == "CREATE_PET":
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "create_pet"
        next_action = "create_pet"

    if scenario_intent == "CHECK_AVAILABILITY":
        main_intent = "BOOKING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        if not database_action:
            database_action = "check_availability"
        if next_action in ("", "clarify_request", "retrieve_service_info", "retrieve_policy"):
            next_action = "check_availability"

    if scenario_intent == "VIEW_BOOKING_STATUS":
        main_intent = "BOOKING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        if not database_action:
            database_action = "check_booking_status"
        if next_action in ("", "clarify_request", "retrieve_service_info", "retrieve_policy"):
            next_action = "check_booking_status"

    if scenario_intent in {
        "CHECK_LOYALTY_POINTS",
        "CHECK_MEMBERSHIP_STATUS",
        "LOYALTY_ACCOUNT_INQUIRY",
        "CHECK_COUPON_ELIGIBILITY",
    }:
        main_intent = "LOYALTY_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        loyalty_actions = {
            "CHECK_LOYALTY_POINTS": "check_loyalty_points",
            "CHECK_MEMBERSHIP_STATUS": "check_membership_status",
            "LOYALTY_ACCOUNT_INQUIRY": "get_loyalty_account",
            "CHECK_COUPON_ELIGIBILITY": "check_coupon_eligibility",
        }
        database_action = loyalty_actions[scenario_intent]
        next_action = database_action

    if scenario_intent in {
        "VIEW_PAYMENT_HISTORY",
        "VIEW_REDEMPTION_HISTORY",
        "VIEW_MESSAGE_HISTORY",
        "VIEW_COMPANY_INFORMATION",
        "VIEW_STAFF_DIRECTORY",
        "VIEW_ACCOUNT_STATUS",
    }:
        main_intent = "ACCOUNT_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        account_actions = {
            "VIEW_PAYMENT_HISTORY": "get_payment_history",
            "VIEW_REDEMPTION_HISTORY": "get_redemption_history",
            "VIEW_MESSAGE_HISTORY": "get_message_history",
            "VIEW_COMPANY_INFORMATION": "get_company_information",
            "VIEW_STAFF_DIRECTORY": "get_staff_directory",
            "VIEW_ACCOUNT_STATUS": "get_customer_profile",
        }
        database_action = account_actions[scenario_intent]
        next_action = database_action

    if scenario_intent == "CUSTOMER_GREETING":
        main_intent = "GREETING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "check_customer_by_phone"
        next_action = "check_customer_by_phone"
        missing_information = []

    if scenario_intent == "REPEAT_LAST_BOOKING":
        main_intent = "BOOKING_INTENT"
        database_action_needed = True
        retrieval_needed = False
        retrieval_source = []
        database_action = "check_last_booking"
        next_action = "check_last_booking"
        missing_information = []

    if scenario_intent == "MAKE_BOOKING":
        main_intent = "BOOKING_INTENT"
        retrieval_needed = False
        retrieval_source = []
        if missing_information:
            database_action_needed = False
            database_action = ""
            next_action = "ask_missing_information"
        else:
            scenario_intent = "CHECK_AVAILABILITY"
            database_action_needed = True
            database_action = "check_availability"
            next_action = "check_availability"

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.40

    if (
        confidence <= 0.0
        and main_intent != "UNKNOWN"
        and scenario_intent != "UNKNOWN"
    ):
        confidence = 0.92

    if main_intent == "UNKNOWN" or scenario_intent == "UNKNOWN":
        main_intent = "UNKNOWN"
        scenario_intent = "UNKNOWN"
        missing_information = []
        retrieval_needed = False
        retrieval_source = []
        database_action_needed = False
        database_action = ""
        next_action = "clarify_request"
        if service_type not in ("GROOMING", "DAYCARE", "BOARDING"):
            service_type = "UNKNOWN"
        if pet_type not in ("DOG", "CAT", "ALL"):
            pet_type = "UNKNOWN"
        confidence = min(confidence, 0.40)

    result = {
        "main_intent": main_intent,
        "scenario_intent": scenario_intent,
        "service_type": service_type,
        "pet_type": pet_type,
        "pet_size": pet_size,
        "pet_height": pet_height,
        "customer_status": customer_status,
        "entities": entities,
        "missing_information": missing_information,
        "retrieval_needed": retrieval_needed,
        "retrieval_source": retrieval_source,
        "database_action_needed": database_action_needed,
        "database_action": database_action,
        "next_action": next_action,
        "confidence": confidence,
        "reason": reason,
    }
    if handoff_reason:
        result["handoff_reason"] = handoff_reason
    return result


_EXPLICIT_HUMAN_HANDOFF = re.compile(
    r"(?:"
    r"speak\s+to\s+(?:(?:your|a|the)\s+)?(?:human|person|someone|agent|staff|manager|supervisor|customer\s+service)|"
    r"talk\s+to\s+(?:(?:your|a|the)\s+)?(?:human|person|someone|agent|staff|manager|supervisor)|"
    r"person\s+in\s+charge|"
    r"customer\s+service|"
    r"(?:real|live)\s+(?:person|human|agent|staff)|"
    r"human\s+(?:agent|assistance|help)|"
    r"staff\s+(?:assistance|help|member)|"
    r"agent\s+assistance|"
    r"call\s+(?:your\s+)?staff|"
    r"get\s+(?:someone|somebody)\s+to\s+help|"
    r"(?:connect|transfer)\s+me\s+(?:to|with)\s+(?:a\s+)?(?:human|person|staff|manager|agent)|"
    r"(?:your\s+)?(?:bot|robot)\s+(?:can(?:not|'t)|cannot)\s+(?:solve|help|understand|do\s+this)|"
    r"(?:not|isn't|isnt)\s+(?:a\s+)?(?:bot|robot)|"
    r"(?:want|need)\s+(?:to\s+speak\s+to\s+|to\s+talk\s+to\s+|)(?:(?:your|a|the)\s+)?manager|"
    r"complaint.*(?:manager|supervisor|person|staff)|"
    r"manager\s+please|"
    r"pass\s+(?:me\s+)?to\s+(?:a\s+)?(?:human|person|staff|manager)"
    r")",
    re.I,
)

_MEDICAL_DIAGNOSIS_HANDOFF = re.compile(
    r"\b("
    r"what\s+medicine\s+should|"
    r"what\s+medication\s+should|"
    r"give\s+my\s+(?:dog|cat|pet)\s+(?:medicine|medication|pills?)|"
    r"diagnos(?:e|is|ing)|"
    r"prescrib(?:e|ing|tion)|"
    r"dosage\s+for|"
    r"treat\s+my\s+(?:dog|cat|pet)(?:'s)?\s+(?:fever|illness|sickness|pain)"
    r")\b",
    re.I,
)


def is_explicit_human_handoff_request(message: str) -> bool:
    text = _normalize_message_text(message)
    if not text:
        return False
    return bool(_EXPLICIT_HUMAN_HANDOFF.search(text))


def is_medical_diagnosis_handoff_request(message: str) -> bool:
    text = _normalize_message_text(message)
    if not text:
        return False
    if any(
        keyword in text
        for keyword in ("vaccination", "vaccine", "health requirement", "health document", "vet requirement")
    ):
        return False
    return bool(_MEDICAL_DIAGNOSIS_HANDOFF.search(text))


def build_human_handoff_intent(message: str, *, handoff_reason: str = "EXPLICIT_HUMAN_REQUEST") -> dict:
    del message
    return normalize_intent_result(
        {
            "main_intent": "UNKNOWN",
            "scenario_intent": "UNKNOWN",
            "service_type": "UNKNOWN",
            "pet_type": "UNKNOWN",
            "pet_size": "UNKNOWN",
            "pet_height": "",
            "customer_status": "UNKNOWN",
            "entities": {},
            "missing_information": [],
            "retrieval_needed": False,
            "retrieval_source": [],
            "database_action_needed": False,
            "database_action": "",
            "next_action": "handoff_to_human",
            "confidence": 0.99,
            "reason": handoff_reason,
            "handoff_reason": handoff_reason,
        }
    )

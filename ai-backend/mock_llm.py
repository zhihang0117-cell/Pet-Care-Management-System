"""
Mock LLM intent detection for Pawfect backend pipeline testing.

Simulates Query JSON Prompt output for local testing and fallback.
"""

from intent_schema import RETRIEVAL_SOURCE_BY_SCENARIO, normalize_intent_result

OUT_OF_SCOPE_PETS = ["rabbit", "bird", "hamster", "parrot", "fish", "turtle", "guinea pig"]


def _detect_pet_type(message_lower: str) -> str:
    if any(animal in message_lower for animal in OUT_OF_SCOPE_PETS):
        return "UNKNOWN"

    has_dog = "dog" in message_lower
    has_cat = "cat" in message_lower

    if has_dog and has_cat:
        return "UNKNOWN"
    if has_dog:
        return "DOG"
    if has_cat:
        return "CAT"
    return "UNKNOWN"


def _detect_service_type(message_lower: str) -> str:
    if "groom" in message_lower or "grooming" in message_lower:
        return "GROOMING"
    if "daycare" in message_lower or "day care" in message_lower:
        return "DAYCARE"
    if any(keyword in message_lower for keyword in ["boarding", "hotel", "overnight"]):
        return "BOARDING"
    if any(
        keyword in message_lower
        for keyword in [
            "business hours",
            "opening hours",
            "closing time",
            "location",
            "address",
            "contact",
            "phone",
            "general enquiry",
            "general inquiry",
        ]
    ):
        return "GENERAL"
    return "UNKNOWN"


def _build_result(
    main_intent: str,
    scenario_intent: str,
    service_type: str,
    pet_type: str,
    next_action: str,
    confidence: float,
    missing_information: list | None = None,
    retrieval_needed: bool = False,
    database_action_needed: bool = False,
    database_action: str = "",
    reason: str = "",
) -> dict:
    retrieval_source = list(RETRIEVAL_SOURCE_BY_SCENARIO.get(scenario_intent, []))
    return normalize_intent_result(
        {
            "main_intent": main_intent,
            "scenario_intent": scenario_intent,
            "service_type": service_type,
            "pet_type": pet_type,
            "pet_size": "UNKNOWN",
            "pet_height": "",
            "customer_status": "UNKNOWN",
            "entities": {},
            "missing_information": missing_information or [],
            "retrieval_needed": retrieval_needed,
            "retrieval_source": retrieval_source,
            "database_action_needed": database_action_needed,
            "database_action": database_action,
            "next_action": next_action,
            "confidence": confidence,
            "reason": reason,
        }
    )


def _match_intent(message_lower: str) -> tuple | None:
    """
    Match message against keyword rules in priority order.
    Returns routing tuple or None.
    """
    out_of_scope_patterns = [
        "tell me a joke",
        "sell puppies",
        "train my dog",
        "deliver pet food",
        "what medicine should",
        "give my cat",
        "give my dog",
        "treat my dog",
        "treat my cat",
        "dog's fever",
        "dog fever",
        "cat fever",
    ]
    if any(pattern in message_lower for pattern in out_of_scope_patterns):
        return (
            "UNKNOWN",
            "UNKNOWN",
            "clarify_request",
            0.35,
            False,
            False,
            "",
            "Message is outside Pawfect service scope",
        )

    if any(
        keyword in message_lower
        for keyword in ["medicine", "medication", "diagnose", "prescribe", "dosage"]
    ) and not any(
        keyword in message_lower
        for keyword in ["vaccination", "vaccine", "health requirement", "health document"]
    ):
        return (
            "UNKNOWN",
            "UNKNOWN",
            "clarify_request",
            0.35,
            False,
            False,
            "",
            "Medical diagnosis or medication advice is out of scope",
        )

    if any(
        keyword in message_lower
        for keyword in [
            "cancellation policy",
            "late cancellation",
            "refund",
        ]
    ) or ("cancellation" in message_lower and "policy" in message_lower):
        return (
            "POLICY_INTENT",
            "CANCELLATION_POLICY",
            "retrieve_policy",
            0.90,
            True,
            False,
            "",
            "Customer asked about cancellation policy",
        )

    if any(
        keyword in message_lower
        for keyword in ["how do i earn", "earn points", "when do points expire", "loyalty policy"]
    ):
        return (
            "POLICY_INTENT",
            "LOYALTY_POLICY",
            "retrieve_policy",
            0.88,
            True,
            False,
            "",
            "Customer asked about loyalty policy rules",
        )

    if any(
        keyword in message_lower
        for keyword in [
            "how many points",
            "point balance",
            "my points",
            "loyalty points",
            "points balance",
            "rewards balance",
            "enough points",
            "check my loyalty",
            "check my rewards",
        ]
    ):
        return (
            "LOYALTY_INTENT",
            "CHECK_LOYALTY_POINTS",
            "check_loyalty_points",
            0.90,
            False,
            True,
            "check_loyalty_points",
            "Customer asked about personal loyalty points balance",
        )

    if "cancel" in message_lower and "policy" not in message_lower and any(
        keyword in message_lower for keyword in ["booking", "appointment"]
    ):
        return (
            "BOOKING_INTENT",
            "CANCEL_BOOKING",
            "cancel_booking",
            0.92,
            False,
            True,
            "cancel_booking",
            "Customer wants to cancel an existing booking",
        )

    if any(
        keyword in message_lower
        for keyword in ["reschedule", "change time", "change date", "move my appointment"]
    ):
        return (
            "BOOKING_INTENT",
            "RESCHEDULE_BOOKING",
            "reschedule_booking",
            0.90,
            False,
            True,
            "reschedule_booking",
            "Customer wants to reschedule an existing booking",
        )

    if "use" in message_lower and "points" in message_lower:
        return (
            "LOYALTY_INTENT",
            "REDEEM_REWARD",
            "redeem_reward",
            0.90,
            False,
            True,
            "redeem_reward",
            "Customer wants to redeem loyalty points",
        )

    if any(
        keyword in message_lower
        for keyword in [
            "status",
            "my booking",
            "my appointment",
            "upcoming booking",
            "appointment confirmed",
            "check my booking",
        ]
    ):
        return (
            "BOOKING_INTENT",
            "VIEW_BOOKING_STATUS",
            "check_booking_status",
            0.88,
            False,
            True,
            "check_booking_status",
            "Customer wants to view booking status",
        )

    if any(
        keyword in message_lower
        for keyword in ["available", "availability", "slot"]
    ):
        return (
            "BOOKING_INTENT",
            "CHECK_AVAILABILITY",
            "check_availability",
            0.88,
            False,
            True,
            "check_availability",
            "Customer asked about service availability",
        )

    if any(
        keyword in message_lower
        for keyword in ["book", "booking", "appointment", "reserve"]
    ):
        return (
            "BOOKING_INTENT",
            "MAKE_BOOKING",
            "ask_missing_information",
            0.92,
            False,
            False,
            "",
            "Customer wants to make a booking",
        )

    if any(
        keyword in message_lower
        for keyword in ["price", "cost", "fee", "how much", "package"]
    ):
        return (
            "POLICY_INTENT",
            "SERVICE_INFORMATION",
            "retrieve_service_info",
            0.88,
            True,
            False,
            "",
            "Customer asked about service information or pricing",
        )

    if any(
        keyword in message_lower
        for keyword in ["vaccine", "vaccination", "health requirement"]
    ):
        return (
            "POLICY_INTENT",
            "VET_REQUIREMENT",
            "retrieve_policy",
            0.88,
            True,
            False,
            "",
            "Customer asked about vaccination or health requirements",
        )

    if any(
        keyword in message_lower
        for keyword in ["grooming rules", "grooming rule", "grooming requirement", "grooming policy"]
    ) or ("rules" in message_lower and ("groom" in message_lower or "grooming" in message_lower)):
        return (
            "POLICY_INTENT",
            "GROOMING_POLICY",
            "retrieve_policy",
            0.85,
            True,
            False,
            "",
            "Customer asked about grooming rules or requirements",
        )

    if any(
        keyword in message_lower
        for keyword in ["daycare rules", "day care rules", "daycare rule", "daycare requirement", "daycare policy"]
    ) or ("rules" in message_lower and ("daycare" in message_lower or "day care" in message_lower)):
        return (
            "POLICY_INTENT",
            "DAYCARE_POLICY",
            "retrieve_policy",
            0.85,
            True,
            False,
            "",
            "Customer asked about daycare rules or requirements",
        )

    if any(
        keyword in message_lower
        for keyword in ["boarding rules", "boarding rule", "boarding requirement", "boarding policy"]
    ) or ("rules" in message_lower and "boarding" in message_lower):
        return (
            "POLICY_INTENT",
            "BOARDING_POLICY",
            "retrieve_policy",
            0.85,
            True,
            False,
            "",
            "Customer asked about boarding rules or requirements",
        )

    if any(
        keyword in message_lower
        for keyword in ["rules", "requirement", "requirements", "guidelines"]
    ):
        if "groom" in message_lower or "grooming" in message_lower:
            scenario = "GROOMING_POLICY"
        elif "daycare" in message_lower or "day care" in message_lower:
            scenario = "DAYCARE_POLICY"
        elif any(keyword in message_lower for keyword in ["boarding", "hotel", "overnight"]):
            scenario = "BOARDING_POLICY"
        else:
            scenario = "GENERAL_POLICY"
        return (
            "POLICY_INTENT",
            scenario,
            "retrieve_policy",
            0.85,
            True,
            False,
            "",
            "Customer asked about service rules or requirements",
        )

    if any(
        keyword in message_lower
        for keyword in ["redeem points", "use points", "redeem my points"]
    ):
        return (
            "LOYALTY_INTENT",
            "REDEEM_REWARD",
            "check_loyalty_account",
            0.90,
            False,
            True,
            "check_loyalty_account",
            "Customer wants to redeem loyalty rewards",
        )

    if any(
        keyword in message_lower
        for keyword in ["membership status", "am i a member", "check my membership"]
    ):
        return (
            "LOYALTY_INTENT",
            "CHECK_MEMBERSHIP_STATUS",
            "check_membership_status",
            0.90,
            False,
            True,
            "check_membership_status",
            "Customer asked about membership status",
        )

    return None


def mock_llm_intent_detection(message: str) -> dict:
    """Simulate Query JSON Prompt output using simple keyword rules."""
    message_lower = message.lower().strip()
    service_type = _detect_service_type(message_lower)
    pet_type = _detect_pet_type(message_lower)

    match = _match_intent(message_lower)
    if match:
        (
            main_intent,
            scenario_intent,
            next_action,
            confidence,
            retrieval_needed,
            database_action_needed,
            database_action,
            reason,
        ) = match
        return _build_result(
            main_intent=main_intent,
            scenario_intent=scenario_intent,
            service_type=service_type,
            pet_type=pet_type,
            next_action=next_action,
            confidence=confidence,
            retrieval_needed=retrieval_needed,
            database_action_needed=database_action_needed,
            database_action=database_action,
            reason=reason,
        )

    return _build_result(
        main_intent="UNKNOWN",
        scenario_intent="UNKNOWN",
        service_type="UNKNOWN",
        pet_type="UNKNOWN",
        next_action="clarify_request",
        confidence=0.40,
        reason="No supported intent matched",
    )

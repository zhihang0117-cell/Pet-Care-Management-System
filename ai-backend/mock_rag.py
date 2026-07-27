"""
Mock RAG retrieval for Pawfect backend pipeline testing.

Simulates the chunk list that real RAG will return later.
Real RAG will use BGE-M3 embeddings + Supabase pgvector.
"""


def mock_rag_retrieve(user_message: str, intent_json: dict, route: str) -> list:
    """
    Simulate RAG retrieval using route and scenario_intent keyword rules.

    Args:
        user_message: The customer's original message (reserved for future use).
        intent_json: Output from mock_llm_intent_detection() or real LLM.
        route: The route decided by route_intent().

    Returns:
        A list of mock RAG chunks, or an empty list if RAG is not needed.
    """
    scenario_intent = intent_json.get("scenario_intent", "UNKNOWN")

    # CALL_KNOWLEDGE_RAG — policy/knowledge questions
    if route == "CALL_KNOWLEDGE_RAG":
        if scenario_intent == "CANCELLATION_POLICY":
            return [
                {
                    "chunk_id": "mock_cancellation_policy_001",
                    "text": "Customers may cancel appointments, but late cancellations may be subject to cancellation rules depending on the appointment time.",
                    "source": "Mock Cancellation Policy",
                    "score": 0.90,
                }
            ]

        if scenario_intent == "SERVICE_INFORMATION":
            return [
                {
                    "chunk_id": "mock_service_information_001",
                    "text": "Service prices depend on the selected service type, pet type, and pet size. The system should retrieve the relevant service price before replying.",
                    "source": "Mock Service Information",
                    "score": 0.88,
                }
            ]

        if scenario_intent == "VET_REQUIREMENT":
            return [
                {
                    "chunk_id": "mock_vet_requirement_001",
                    "text": "Pets may need to meet vaccination and health requirements before daycare or boarding services.",
                    "source": "Mock Veterinary Requirement",
                    "score": 0.89,
                }
            ]

        if scenario_intent == "GROOMING_POLICY":
            return [
                {
                    "chunk_id": "mock_grooming_policy_001",
                    "text": "Grooming rules and requirements may vary by pet type and service package. Customers should follow the relevant grooming guidelines before the appointment.",
                    "source": "Mock Grooming Policy",
                    "score": 0.87,
                }
            ]

        if scenario_intent == "DAYCARE_POLICY":
            return [
                {
                    "chunk_id": "mock_daycare_policy_001",
                    "text": "Daycare rules and requirements may vary by pet type and session type. Customers should follow the relevant daycare guidelines before the appointment.",
                    "source": "Mock Daycare Policy",
                    "score": 0.87,
                }
            ]

        if scenario_intent == "BOARDING_POLICY":
            return [
                {
                    "chunk_id": "mock_boarding_policy_001",
                    "text": "Boarding rules and requirements may vary by pet type and stay duration. Customers should follow the relevant boarding guidelines before the appointment.",
                    "source": "Mock Boarding Policy",
                    "score": 0.87,
                }
            ]

        if scenario_intent == "GENERAL_POLICY":
            return [
                {
                    "chunk_id": "mock_general_policy_001",
                    "text": "Service rules may vary depending on grooming, daycare, or boarding. Customers should follow the relevant service guidelines before the appointment.",
                    "source": "Mock General Policy",
                    "score": 0.87,
                }
            ]

        if scenario_intent == "LOYALTY_POLICY":
            return [
                {
                    "chunk_id": "mock_loyalty_policy_001",
                    "text": "Loyalty points, rewards, expiry, and redemption rules should be checked from the loyalty policy before advising the customer.",
                    "source": "Mock Loyalty Policy",
                    "score": 0.86,
                }
            ]

    # CALL_RAG_AND_DATABASE — booking changes need policy + database
    if route == "CALL_RAG_AND_DATABASE":
        return [
            {
                "chunk_id": "mock_policy_and_action_001",
                "text": "Before cancelling or rescheduling a booking, the system should check the relevant policy and then verify the booking record in the database.",
                "source": "Mock Policy And Database Action Context",
                "score": 0.90,
            }
        ]

    # CALL_RAG_THEN_ASK_MISSING_INFO — booking interruption with supporting info lookup
    if route == "CALL_RAG_THEN_ASK_MISSING_INFO":
        return [
            {
                "chunk_id": "mock_booking_price_interruption_001",
                "text": "Full Grooming package pricing depends on pet size. Basic Grooming covers essential wash and tidy; Full Grooming includes complete groom and styling.",
                "source": "Mock Grooming Price",
                "score": 0.88,
            }
        ]

    # All other routes do not need RAG yet
    return []

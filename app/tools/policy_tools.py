from langchain_core.tools import tool
from typing import Literal

from app.rag.retriever import CompanyRAGRetriever

_retriever = CompanyRAGRetriever()


@tool
def retrieve_policy(
    company_id: str | int,
    query: str,
    service_type: Literal["grooming", "daycare", "boarding", "general"] | None = None,
    pet_type: Literal["cat", "dog"] | None = None,
    pet_size: str | None = None,
) -> list[dict]:
    """
    Retrieve company-specific policy/SOP knowledge through RAG
    (BGE-Large, Supabase pgvector). Pass service_type ("grooming"/"daycare"/
    "boarding") when you already know which service the question is about —
    it narrows the search to that service's chunks. Leave it unset for a
    general/ambiguous customer enquiry that doesn't clearly belong to one
    service; a broader semantic search across everything is more likely to
    find the right chunk than guessing a service_type that turns out wrong.

    Use this for policies, requirements, terms, SOPs, and open-ended company
    knowledge. For bookable packages/prices, use get_booking_service_options
    instead; that tool already performs the correctly filtered RAG catalogue
    lookup and returns the live structured room catalogue where applicable.

    pet_type/pet_size remain available for a genuinely species-specific
    policy query, but do not duplicate a catalogue call just to obtain price.
    """
    return _retriever.search(
        company_id, query, service_type=service_type, pet_type=pet_type, pet_size=pet_size
    )

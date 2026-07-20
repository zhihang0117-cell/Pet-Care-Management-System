"""Metadata schema: allowed values, aliases, and field names."""

from __future__ import annotations

ALLOWED_PET_TYPES = frozenset({"dog", "cat", "all"})
ALLOWED_SERVICE_TYPES = frozenset({"grooming", "boarding", "daycare", "general"})

SERVICE_TYPE_ALIASES: dict[str, str] = {
    "grooming": "grooming",
    "groom": "grooming",
    "grooming_policy": "grooming",
    "boarding": "boarding",
    "overnight": "boarding",
    "daycare": "daycare",
    "day_care": "daycare",
    "day-care": "daycare",
    "general": "general",
    "all": "general",
    "all_services": "general",
    "cancellation": "general",
    "cancellation_policy": "general",
    "loyalty": "general",
    "loyalty_policy": "general",
    # legacy: anything previously tagged unknown is treated as general
    "unknown": "general",
}

PET_TYPE_ALIASES: dict[str, str] = {
    "dog": "dog",
    "dogs": "dog",
    "puppy": "dog",
    "puppies": "dog",
    "cat": "cat",
    "cats": "cat",
    "kitten": "cat",
    "kittens": "cat",
    "all": "all",
    "both": "all",
    "any": "all",
    "unknown": "all",
    "": "all",
}

# Canonical eval row fields persisted after CSV upload
EVAL_ROW_FIELDS = (
    "eval_id",
    "seed_id",
    "user_message",
    "dataset_type",
    "service_type",
    "expected_pet_type",
    "service_info",
)

# CSV / legacy column names -> canonical eval fields
EVAL_CSV_COLUMN_ALIASES: dict[str, str] = {
    "expected_dataset_type": "dataset_type",
    "file_name": "dataset_type",
    "source_pdf": "dataset_type",
    "source_file": "dataset_type",
    "expected_dataset": "dataset_type",
    "primary_dataset": "dataset_type",
    "expected_service_type": "service_type",
    "scenario_type": "service_type",
    "scenario_intent": "service_type",
    "pet_type": "expected_pet_type",
    "expected_service_info": "service_info",
}

EVAL_DATASET_FIELDS = ("dataset_type", "expected_dataset_type", "file_name", "source_file", "source_pdf")
EVAL_SERVICE_FIELDS = ("service_type", "expected_service_type", "scenario_type", "scenario_intent")
EVAL_PET_FIELDS = ("expected_pet_type", "pet_type")
EVAL_SERVICE_INFO_FIELDS = ("service_info", "expected_service_info")
EVAL_QUERY_TEXT_FIELDS = ("user_message", "query", "question", "prompt")

CHUNK_DATASET_FIELDS = ("dataset_type", "source_file", "document_file_name", "file_name")

LEGACY_METADATA_KEYS = (
    "file_name",
    "scenario_type",
    "scenario_tags",
    "service_types",
    "section_tags",
    "section_tag",
    "main_intent",
    "scenario_intent",
    "requires_rag",
    "expected_dataset",
    "expected_section_tag",
    "expected_scenario_tag",
    "retrieval_type",
    "eval_scope",
    "relevance_rule",
    "metadata_tightening_note",
    "actual_scenario",
)

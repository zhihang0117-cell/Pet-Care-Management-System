"""Smoke tests for production RAG wrappers (no Supabase required)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import MODELS, PRODUCTION_MODEL_KEY
from app.database.supabase import PRODUCTION_MATCH_RPC
from app.rag.chunking import (
    build_production_chunk_id,
    chunk_docx_pages,
    validate_document_type,
)
from app.rag.ingestion import validate_docx_file
from app.rag.retrieval import build_production_metadata
from app.main import app

AI_BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_002 = AI_BACKEND_ROOT / "supabase/migrations/002_add_document_id_to_chunks_bge_large.sql"
SUPABASE_STORE_SOURCE = AI_BACKEND_ROOT / "app/services/supabase_store.py"


class ProductionPipelineTests(unittest.TestCase):
    def test_validate_document_type_accepts_business_categories(self):
        self.assertEqual(validate_document_type("policies"), "policies")
        self.assertEqual(
            validate_document_type("service_information"),
            "service_information",
        )

    def test_validate_document_type_rejects_file_format_labels(self):
        with self.assertRaises(ValueError):
            validate_document_type("docx")

    def test_build_production_chunk_id_is_deterministic(self):
        chunk_id = build_production_chunk_id(
            "doc-123",
            section_id="3.1",
            part_number=2,
        )
        self.assertEqual(chunk_id, "doc_123__3_1_2")
        self.assertEqual(
            build_production_chunk_id("doc-123", section_id="3.1", part_number=1),
            "doc_123__3_1",
        )

    def test_validate_docx_file_checks_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = Path(handle.name)
        try:
            with self.assertRaises(ValueError):
                validate_docx_file(path)
        finally:
            path.unlink(missing_ok=True)

    def test_build_production_metadata_without_eval_row(self):
        meta = build_production_metadata(
            123,
            "How much is dog grooming?",
            service_type=None,
            pet_type=None,
        )
        self.assertEqual(meta["company_id"], 123)
        self.assertEqual(meta["service_info"], "")
        self.assertIn(meta["service_type"], {"grooming", "general", "boarding", "daycare"})

    def test_production_model_config_unchanged(self):
        cfg = MODELS[PRODUCTION_MODEL_KEY]
        self.assertEqual(cfg["model_name"], "BAAI/bge-large-en-v1.5")
        self.assertEqual(cfg["dimensions"], 1024)
        self.assertEqual(
            cfg["query_prefix"],
            "Represent this sentence for searching relevant passages: ",
        )
        self.assertEqual(cfg["document_prefix"], "")
        self.assertEqual(cfg["table"], "chunks_bge_large")
        self.assertEqual(cfg["rpc"], "match_chunks_bge_large")

    def test_experiment_and_production_routes_coexist(self):
        paths = set(app.openapi()["paths"].keys())
        self.assertIn("/health", paths)
        self.assertIn("/results/comparison", paths)
        self.assertIn("/results/dm-test", paths)
        self.assertIn("/api/documents/process", paths)
        self.assertIn("/api/rag/retrieve", paths)
        self.assertEqual(
            len([route for route in app.routes if getattr(route, "path", "") == "/health"]),
            1,
        )

    def test_production_schemas_use_company_id(self):
        from app.schemas.documents import ProcessDocumentRequest
        from app.schemas.rag import RetrieveRequest

        process_req = ProcessDocumentRequest(
            company_id=123,
            document_id="doc-1",
            document_type="policies",
            storage_bucket="docs",
            storage_path="123/policies.docx",
        )
        retrieve_req = RetrieveRequest(
            company_id=123,
            query="boarding check-out time",
        )
        self.assertEqual(process_req.company_id, 123)
        self.assertEqual(retrieve_req.company_id, 123)
        self.assertFalse(hasattr(process_req, "tenant_id"))

    def test_migration_uses_bigint_safe_company_filter(self):
        sql = MIGRATION_002.read_text(
            encoding="utf-8"
        )
        self.assertNotIn("c.company_id = filter_tenant", sql)
        self.assertIn("filter_tenant::bigint", sql)
        self.assertIn("metadata->>'tenant_id' = filter_tenant", sql)
        self.assertIn("p_company_id bigint", sql)
        self.assertIn("where company_id = p_company_id", sql)
        self.assertIn("match_chunks_bge_large_production", sql)
        self.assertIn("match_chunks_bge_large(", sql)
        self.assertIn("filter_tenant text", sql)
        self.assertIn("c.document_id", sql)
        self.assertRegex(
            sql,
            r"match_chunks_bge_large_production[\s\S]*document_id text",
        )

    @patch("app.database.supabase.get_supabase_client")
    @patch("app.database.supabase.get_production_embedder")
    def test_production_retrieval_uses_bigint_rpc(self, mock_embedder, mock_client):
        from app.database.supabase import ProductionVectorStore

        mock_embedder.return_value.embed_query.return_value = [0.0] * 1024
        mock_client.return_value.rpc.return_value.execute.return_value.data = []

        store = ProductionVectorStore(mock_client.return_value, 123)
        store.similarity_search("boarding check-out", k=5)

        rpc_name, rpc_args = mock_client.return_value.rpc.call_args.args
        self.assertEqual(rpc_name, PRODUCTION_MATCH_RPC)
        self.assertEqual(rpc_args["p_company_id"], 123)
        self.assertNotIn("filter_tenant", rpc_args)

    def test_experiment_rpc_call_shape_remains_filter_tenant(self):
        source = SUPABASE_STORE_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"filter_tenant": self.tenant_id', source)
        sql = MIGRATION_002.read_text(
            encoding="utf-8"
        )
        self.assertIn("filter_tenant text", sql)

    @patch("app.database.supabase.get_supabase_client")
    @patch("app.database.supabase.get_production_embedder")
    def test_replace_document_chunks_fails_before_delete_when_rpc_missing(
        self,
        mock_embedder,
        mock_client,
    ):
        from app.database.supabase import ProductionVectorStore

        mock_embedder.return_value.embed_documents.return_value = [[0.1] * 1024]
        mock_client.return_value.rpc.return_value.execute.side_effect = Exception(
            "Could not find the function public.replace_document_chunks_bge_large"
        )

        store = ProductionVectorStore(mock_client.return_value, 123)
        chunks = [
            {
                "chunk_id": "doc1__policies_1",
                "company_id": 123,
                "document_id": "doc1",
                "document_type": "policies",
                "dataset_type": "policies",
                "text": "Boarding check-in time is 9am.",
                "pet_type": "all",
                "service_type": "boarding",
                "service_info": "Check-In & Check-Out Time",
                "main_header": "Boarding",
                "sub_header": "Check-In & Check-Out Time",
                "section_path": "2 Boarding > 2.4 Check-In & Check-Out Time",
            }
        ]

        with self.assertRaises(RuntimeError):
            store.replace_document_chunks("doc1", chunks)

    @patch("app.database.supabase.get_supabase_client")
    @patch("app.database.supabase.get_production_embedder")
    def test_retrieval_returns_table_document_id(self, mock_embedder, mock_client):
        """a) Prefer chunks_bge_large.document_id column over empty metadata."""
        from app.database.supabase import ProductionVectorStore
        from app.rag.pipeline import retrieve_chunks

        mock_embedder.return_value.embed_query.return_value = [0.0] * 1024
        mock_client.return_value.rpc.return_value.execute.return_value.data = [
            {
                "chunk_id": "grooming_policy_test_extended_001__3_1",
                "document_id": "grooming_policy_test_extended-001",
                "content": "Full Grooming includes bath, haircut, and nail trim.",
                "metadata": {
                    "chunk_id": "grooming_policy_test_extended_001__3_1",
                    "service_type": "grooming",
                    # Intentionally omit document_id from metadata jsonb.
                },
                "similarity": 0.91,
            }
        ]

        store = ProductionVectorStore(mock_client.return_value, 123)
        with patch(
            "app.rag.retrieval.ProductionVectorStore.for_company",
            return_value=store,
        ):
            result = retrieve_chunks(
                "What is included in Full Grooming?",
                company_id=123,
                top_k=5,
                service_type="grooming",
            )

        self.assertTrue(result["chunks"])
        self.assertEqual(
            result["chunks"][0]["document_id"],
            "grooming_policy_test_extended-001",
        )
        self.assertNotEqual(result["chunks"][0]["document_id"], "")

    @patch("app.rag.chunking.chunk_from_pages")
    def test_production_chunk_ids_exclude_temp_filenames(self, mock_chunk_from_pages):
        """b) Production IDs must not embed tempfile stems like tmp8z84uvor."""
        mock_chunk_from_pages.return_value = [
            {
                "chunk_id": "tmp8z84uvor_3_1",
                "section_id": "3.1",
                "text": "Full Grooming includes bath and haircut.",
                "source_file": "tmp8z84uvor.docx",
                "document_file_name": "tmp8z84uvor.docx",
                "pet_type": "all",
                "service_type": "grooming",
                "service_info": "Full Grooming",
                "main_header": "Grooming Service",
                "sub_header": "Full Grooming",
                "section_path": "3 Grooming Service > 3.1 Full Grooming",
                "section_title": "Full Grooming",
            }
        ]

        chunks = chunk_docx_pages(
            pages=[],
            company_id=123,
            document_id="grooming_policy_test_extended-001",
            document_type="policies",
        )

        chunk_id = chunks[0]["chunk_id"]
        self.assertEqual(
            chunk_id,
            "grooming_policy_test_extended_001__3_1",
        )
        self.assertNotIn("tmp8z84uvor", chunk_id)
        self.assertNotIn("tmp", chunk_id.lower())

    @patch("app.rag.chunking.chunk_from_pages")
    def test_reprocessing_same_document_yields_identical_chunk_ids(
        self,
        mock_chunk_from_pages,
    ):
        """c) Same document_id + section identity + parts → same chunk_ids."""
        first_pass = [
            {
                "chunk_id": "tmpaaa_3_1",
                "section_id": "3.1",
                "text": "Part one of Full Grooming.",
                "source_file": "tmpaaa.docx",
                "pet_type": "all",
                "service_type": "grooming",
                "service_info": "Full Grooming",
                "main_header": "Grooming Service",
                "sub_header": "Full Grooming",
                "section_path": "3 Grooming Service > 3.1 Full Grooming",
                "section_title": "Full Grooming",
            },
            {
                "chunk_id": "tmpaaa_3_1_2",
                "section_id": "3.1",
                "text": "Part two of Full Grooming.",
                "source_file": "tmpaaa.docx",
                "pet_type": "all",
                "service_type": "grooming",
                "service_info": "Full Grooming",
                "main_header": "Grooming Service",
                "sub_header": "Full Grooming",
                "section_path": "3 Grooming Service > 3.1 Full Grooming",
                "section_title": "Full Grooming",
            },
        ]
        second_pass = [
            {
                **first_pass[0],
                "chunk_id": "tmpzzz_3_1",
                "source_file": "tmpzzz.docx",
            },
            {
                **first_pass[1],
                "chunk_id": "tmpzzz_3_1_2",
                "source_file": "tmpzzz.docx",
            },
        ]

        mock_chunk_from_pages.return_value = first_pass
        ids_a = [
            c["chunk_id"]
            for c in chunk_docx_pages(
                pages=[],
                company_id=123,
                document_id="grooming_policy_test_extended-001",
                document_type="policies",
            )
        ]

        mock_chunk_from_pages.return_value = second_pass
        ids_b = [
            c["chunk_id"]
            for c in chunk_docx_pages(
                pages=[],
                company_id=123,
                document_id="grooming_policy_test_extended-001",
                document_type="policies",
            )
        ]

        self.assertEqual(ids_a, ids_b)
        self.assertEqual(
            ids_a,
            [
                "grooming_policy_test_extended_001__3_1",
                "grooming_policy_test_extended_001__3_1_2",
            ],
        )


if __name__ == "__main__":
    unittest.main()

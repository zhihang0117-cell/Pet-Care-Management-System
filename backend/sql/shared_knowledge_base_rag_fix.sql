-- Review-only migration: make company_id IS NULL rows in chunks_bge_large
-- actually retrievable as shared/global knowledge. Do NOT run automatically.
-- Apply manually in the Supabase SQL editor after review.
--
-- Confirmed live (2026-08-07): chunks_bge_large already contains 120 real
-- rows with company_id = NULL, uploaded as Shared_Cat_Knowledge_Base.docx /
-- Shared_Dog_Knowledge_Base.docx / Shared_Emergency_Knowledge_Base.docx /
-- Shared_Veterinary_Knowledge_Base.docx (sourced from the MSD Veterinary
-- Manual, tagged metadata.knowledge_scope = "shared"). The intended model
-- is: company_id IS NULL means visible to every company; a real company_id
-- scopes a row to only that company.
--
-- That NULL-means-shared half was never implemented in the deployed
-- match_chunks_bge_large RPC (see 002_add_document_id_to_chunks_bge_large.sql)
-- — its WHERE clause only matches c.company_id = filter_tenant::bigint (or a
-- legacy metadata tenant_id/company_id string match). In standard SQL,
-- `NULL = 1` is never true, so every one of those 120 shared rows has been
-- completely unreachable by any company's RAG query since the day they were
-- uploaded — a retrieval-SQL gap, not a missing-content problem.
--
-- Fix: OR in `c.company_id IS NULL` so a shared row is always considered
-- for every company's query, while a real company_id row still only ever
-- matches that one company (unchanged behavior — this migration ADDS
-- visibility for shared rows only, it never narrows anything).

create or replace function match_chunks_bge_large(
  query_embedding vector(1024),
  match_count int,
  filter_tenant text,
  filter_metadata jsonb default '{}'
) returns table (chunk_id text, content text, metadata jsonb, similarity float)
language sql stable as $$
  select c.chunk_id, c.content, c.metadata,
         1 - (c.embedding <=> query_embedding) as similarity
  from chunks_bge_large c
  where (
      c.company_id = (
        case
          when filter_tenant ~ '^[0-9]+$' then filter_tenant::bigint
          else null
        end
      )
      -- Shared/global knowledge base rows (company_id intentionally never
      -- set) — visible to every company's query, regardless of filter_tenant.
      or c.company_id is null
      or c.metadata->>'tenant_id' = filter_tenant
      or c.metadata->>'company_id' = filter_tenant
    )
    and c.metadata @> filter_metadata
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

comment on function match_chunks_bge_large(vector, int, text, jsonb) is
  'Compatibility BGE-Large search. filter_tenant remains text, safely cast to bigint company_id when numeric. company_id IS NULL rows are shared knowledge, visible to every tenant.';

-- The repository's base definition of match_chunks_bge_large_production in
-- 002_add_document_id_to_chunks_bge_large.sql now has the identical shared-
-- row clause. The reference definition is retained below for review only:
--
-- create or replace function match_chunks_bge_large_production(
--   query_embedding vector(1024),
--   match_count int,
--   p_company_id bigint,
--   filter_metadata jsonb default '{}'
-- ) returns table (
--   chunk_id text, document_id text, content text, metadata jsonb, similarity float
-- )
-- language sql stable as $$
--   select c.chunk_id, c.document_id, c.content, c.metadata,
--          1 - (c.embedding <=> query_embedding) as similarity
--   from chunks_bge_large c
--   where (c.company_id = p_company_id or c.company_id is null)
--     and c.metadata @> filter_metadata
--   order by c.embedding <=> query_embedding
--   limit match_count;
-- $$;

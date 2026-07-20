-- Review-only migration: document ownership + bigint company_id support for BGE-Large.
-- Do NOT run automatically. Apply manually in Supabase SQL editor after review.
--
-- Live schema status (verified before writing this file):
--   - chunks_bge_large.company_id EXISTS
--   - chunks_bge_large.tenant_id DOES NOT EXIST (already renamed)
--   - chunks_bge_large.document_id DOES NOT EXIST
--   - replace_document_chunks_bge_large DOES NOT EXIST
--   - match_chunks_bge_large still accepts parameter name filter_tenant text,
--     but its body still references c.tenant_id (broken after column rename)
--
-- Purpose:
--   1. Add document_id for production reprocessing ownership.
--   2. Add atomic replace RPC scoped to (company_id, document_id).
--   3. Repair match_chunks_bge_large without changing its existing text
--      filter_tenant signature (experiment compatibility).
--   4. Add a production-specific bigint RPC for company_id retrieval.

-- ---------------------------------------------------------------------------
-- Step 1: add document_id column (nullable for legacy/experiment rows)
-- ---------------------------------------------------------------------------
alter table chunks_bge_large
  add column if not exists document_id text;

comment on column chunks_bge_large.document_id is
  'Business document identifier within a company. Required for production writes.';

create index if not exists idx_chunks_bge_large_company_document
  on chunks_bge_large (company_id, document_id);

-- Optional manual backfill (commented out — review before running):
-- update chunks_bge_large
-- set document_id = coalesce(metadata->>'document_id', 'legacy-unknown')
-- where document_id is null;

-- ---------------------------------------------------------------------------
-- Step 2: repair existing match_chunks_bge_large for experiment compatibility
-- ---------------------------------------------------------------------------
-- BEFORE signature (live):
--   match_chunks_bge_large(query_embedding vector(1024), match_count int,
--                          filter_tenant text, filter_metadata jsonb)
--
-- AFTER signature (kept the same):
--   match_chunks_bge_large(query_embedding vector(1024), match_count int,
--                          filter_tenant text, filter_metadata jsonb)
--
-- The body now compares bigint = bigint by safely casting filter_tenant only
-- when it contains digits. For non-numeric legacy experiment callers, it
-- also falls back to metadata->>'tenant_id' / metadata->>'company_id' when
-- those legacy metadata values exist.
-- Signature and return shape remain the same.

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
      or c.metadata->>'tenant_id' = filter_tenant
      or c.metadata->>'company_id' = filter_tenant
    )
    and c.metadata @> filter_metadata
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

comment on function match_chunks_bge_large(vector, int, text, jsonb) is
  'Compatibility BGE-Large search. filter_tenant remains text, safely cast to bigint company_id when numeric.';

-- ---------------------------------------------------------------------------
-- Step 3: production bigint company_id search RPC
-- ---------------------------------------------------------------------------
-- Production code should call this RPC instead of the compatibility RPC above.
-- This avoids bigint=text comparisons and avoids relying on text casting.

create or replace function match_chunks_bge_large_production(
  query_embedding vector(1024),
  match_count int,
  p_company_id bigint,
  filter_metadata jsonb default '{}'
) returns table (
  chunk_id text,
  document_id text,
  content text,
  metadata jsonb,
  similarity float
)
language sql stable as $$
  select c.chunk_id,
         c.document_id,
         c.content,
         c.metadata,
         1 - (c.embedding <=> query_embedding) as similarity
  from chunks_bge_large c
  where c.company_id = p_company_id
    and c.metadata @> filter_metadata
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

comment on function match_chunks_bge_large_production(vector, int, bigint, jsonb) is
  'Production BGE-Large similarity search scoped by bigint company_id. Returns table document_id.';

-- ---------------------------------------------------------------------------
-- Step 4: atomic replace RPC for one (company_id, document_id)
-- ---------------------------------------------------------------------------
-- p_chunks JSON array shape:
-- [
--   {
--     "chunk_id": "doc-uuid__policies_1",
--     "content": "chunk text",
--     "metadata": { ... },
--     "embedding": [0.01, 0.02, ...]   -- length 1024
--   }
-- ]

create or replace function replace_document_chunks_bge_large(
  p_company_id bigint,
  p_document_id text,
  p_chunks jsonb
)
returns integer
language plpgsql
as $$
declare
  inserted_count integer;
begin
  if p_company_id is null then
    raise exception 'company_id is required';
  end if;
  if p_document_id is null or btrim(p_document_id) = '' then
    raise exception 'document_id is required';
  end if;
  if p_chunks is null or jsonb_typeof(p_chunks) <> 'array' then
    raise exception 'p_chunks must be a JSON array';
  end if;

  delete from chunks_bge_large
  where company_id = p_company_id
    and document_id = p_document_id;

  insert into chunks_bge_large (
    company_id,
    document_id,
    chunk_id,
    content,
    metadata,
    embedding
  )
  select
    p_company_id,
    p_document_id,
    r.chunk_id,
    r.content,
    coalesce(r.metadata, '{}'::jsonb),
    (r.embedding)::vector(1024)
  from jsonb_to_recordset(p_chunks) as r(
    chunk_id text,
    content text,
    metadata jsonb,
    embedding double precision[]
  );

  get diagnostics inserted_count = row_count;
  return inserted_count;
end;
$$;

comment on function replace_document_chunks_bge_large(bigint, text, jsonb) is
  'Atomically replace all BGE-Large chunks for (company_id, document_id).';

-- Company-owned source documents used by the production RAG pipeline.
-- Apply this file and ai-backend/supabase/migrations/002_add_document_id_to_chunks_bge_large.sql
-- in the Supabase SQL editor before enabling uploads.

create table if not exists company_documents (
  document_id uuid primary key default gen_random_uuid(),
  company_id bigint not null references companies(company_id) on delete cascade,
  service_type text not null check (service_type in ('grooming', 'boarding', 'daycare', 'general')),
  document_type text not null default 'policies'
    check (document_type in ('policies', 'service_information', 'business_flow_booking', 'veterinary')),
  file_name text not null,
  storage_bucket text not null default 'company-documents',
  storage_path text not null unique,
  mime_type text not null,
  file_size bigint not null check (file_size > 0),
  status text not null default 'pending'
    check (status in ('pending', 'processing', 'indexed', 'failed')),
  chunks_indexed integer,
  error_message text,
  created_at timestamptz not null default now(),
  indexed_at timestamptz
);

create index if not exists idx_company_documents_company_created
  on company_documents (company_id, created_at desc);

alter table company_documents enable row level security;

drop policy if exists company_documents_select_own on company_documents;
create policy company_documents_select_own on company_documents
  for select using (company_id = current_company_id());

-- Browser clients never write this table directly. The authenticated Express
-- API validates manager access and writes with the service role.

create or replace function delete_document_chunks_bge_large(
  p_company_id bigint,
  p_document_id text
) returns integer
language plpgsql
as $$
declare deleted_count integer;
begin
  delete from chunks_bge_large
  where company_id = p_company_id and document_id = p_document_id;
  get diagnostics deleted_count = row_count;
  return deleted_count;
end;
$$;


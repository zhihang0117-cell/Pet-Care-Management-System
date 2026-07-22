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
  storage_bucket text not null default 'business-documents',
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

-- Keep existing installations aligned with the configured private bucket.
alter table company_documents alter column storage_bucket set default 'business-documents';

-- company_documents is now the sole source of truth for source files.
update companies
set settings_json = settings_json - 'policies'
where settings_json ? 'policies';

-- Authenticated browser access is tenant-scoped. The backend service-role
-- client bypasses these policies but still filters every operation by company_id.
drop policy if exists business_documents_select on storage.objects;
create policy business_documents_select on storage.objects
  for select to authenticated
  using (
    bucket_id = 'business-documents'
    and (storage.foldername(name))[1] = current_company_id()::text
  );

drop policy if exists business_documents_insert on storage.objects;
create policy business_documents_insert on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'business-documents'
    and (storage.foldername(name))[1] = current_company_id()::text
    and is_current_account_manager()
  );

drop policy if exists business_documents_update on storage.objects;
create policy business_documents_update on storage.objects
  for update to authenticated
  using (
    bucket_id = 'business-documents'
    and (storage.foldername(name))[1] = current_company_id()::text
    and is_current_account_manager()
  )
  with check (
    bucket_id = 'business-documents'
    and (storage.foldername(name))[1] = current_company_id()::text
    and is_current_account_manager()
  );

drop policy if exists business_documents_delete on storage.objects;
create policy business_documents_delete on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'business-documents'
    and (storage.foldername(name))[1] = current_company_id()::text
    and is_current_account_manager()
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

-- Delete the application row and its vectors in one Postgres transaction.
-- Supabase Storage is external to Postgres and is cleaned up by the backend
-- after this RPC commits; a Storage failure can only leave an orphaned object.
create or replace function delete_company_document_with_chunks(
  p_company_id bigint,
  p_document_id text
) returns integer
language plpgsql
as $$
declare deleted_count integer;
begin
  delete from chunks_bge_large
  where company_id = p_company_id and document_id = p_document_id;

  delete from company_documents
  where company_id = p_company_id and document_id = p_document_id::uuid;
  get diagnostics deleted_count = row_count;

  if deleted_count <> 1 then
    raise exception 'Document not found';
  end if;
  return deleted_count;
end;
$$;

-- If indexing succeeded but its metadata commit failed, make the failure
-- state and vector cleanup atomic so a failed row can never remain searchable.
create or replace function fail_company_document_and_delete_chunks(
  p_company_id bigint,
  p_document_id text,
  p_error_message text
) returns integer
language plpgsql
as $$
declare updated_count integer;
begin
  delete from chunks_bge_large
  where company_id = p_company_id and document_id = p_document_id;

  update company_documents
  set status = 'failed',
      error_message = left(coalesce(p_error_message, 'Indexing failed'), 1000),
      chunks_indexed = null,
      indexed_at = null
  where company_id = p_company_id and document_id = p_document_id::uuid;
  get diagnostics updated_count = row_count;

  if updated_count <> 1 then
    raise exception 'Document not found';
  end if;
  return updated_count;
end;
$$;

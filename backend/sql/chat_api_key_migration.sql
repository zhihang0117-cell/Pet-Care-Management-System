-- Run once in the Supabase SQL editor.
--
-- The Python AI service's /chat endpoint (main.py) previously had NO way to
-- determine which company an incoming message belonged to on a per-request
-- basis at all — it always used RELATIONAL_COMPANY_ID, one hardcoded env var
-- for the entire deployed process. That's fine as long as one Python
-- service instance is deployed per company, but it means this codebase
-- could not safely serve more than one company from a single deployment:
-- there was no mechanism to route an inbound message to the right tenant.
--
-- This table lets a single Python deployment serve multiple companies: each
-- company gets its own X-Chat-Key, and /chat resolves company_id by looking
-- up a SHA-256 digest here. The plaintext credential is never stored in the
-- database — the same "derive tenant from a verified
-- credential, never trust a client-supplied id directly" principle already
-- used by the Node dashboard's requireAuthUser (see backend/DEVELOPER_GUIDE.md
-- §3), not the header-trust pattern that was removed from /api/llm.
--
-- Backward compatible: main.py still falls back to the legacy single
-- CHAT_API_KEY env var (resolving to RELATIONAL_COMPANY_ID) when a
-- presented key doesn't match any row here — an existing single-company
-- deployment keeps working unchanged even before this migration is run, and
-- even after, until you actually add a second company's key.
--
-- When a real WhatsApp webhook is eventually built (see main.py's module
-- docstring), it should resolve company_id from the WEBHOOK PAYLOAD's own
-- verified phone_number_id instead of this table — Meta's webhook signature
-- verification makes that value trustworthy in a way a client header never
-- is. This table is the interim/eval-console-compatible mechanism until then.

create extension if not exists pgcrypto;

create table if not exists company_chat_key (
  company_id bigint not null references companies(company_id) on delete cascade,
  chat_api_key_hash text not null,
  label text,
  created_at timestamptz not null default now(),
  primary key (chat_api_key_hash),
  constraint company_chat_key_hash_format
    check (chat_api_key_hash ~ '^[0-9a-f]{64}$')
);

-- Upgrade the short-lived plaintext draft of this migration if it was
-- applied before hash-only storage was introduced.
alter table company_chat_key add column if not exists chat_api_key_hash text;

do $$
declare
  primary_key_name text;
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'company_chat_key'
      and column_name = 'chat_api_key'
  ) then
    execute $sql$
      update public.company_chat_key
      set chat_api_key_hash = encode(digest(chat_api_key, 'sha256'), 'hex')
      where chat_api_key_hash is null
    $sql$;

    select conname into primary_key_name
    from pg_constraint
    where conrelid = 'public.company_chat_key'::regclass
      and contype = 'p';
    if primary_key_name is not null then
      execute format(
        'alter table public.company_chat_key drop constraint %I',
        primary_key_name
      );
    end if;
    alter table public.company_chat_key drop column chat_api_key;
  end if;
end
$$;

alter table company_chat_key alter column chat_api_key_hash set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.company_chat_key'::regclass
      and contype = 'p'
  ) then
    alter table company_chat_key
      add constraint company_chat_key_pkey primary key (chat_api_key_hash);
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.company_chat_key'::regclass
      and conname = 'company_chat_key_company_id_fkey'
  ) then
    alter table company_chat_key
      add constraint company_chat_key_company_id_fkey
      foreign key (company_id) references companies(company_id) on delete cascade;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.company_chat_key'::regclass
      and conname = 'company_chat_key_hash_format'
  ) then
    alter table company_chat_key
      add constraint company_chat_key_hash_format
      check (chat_api_key_hash ~ '^[0-9a-f]{64}$');
  end if;
end
$$;

create index if not exists company_chat_key_company_id_idx
  on company_chat_key (company_id);

comment on table company_chat_key is
  'Maps a SHA-256 digest of a per-company X-Chat-Key to the company_id /chat should operate as. Plaintext keys must never be stored.';

alter table company_chat_key enable row level security;
revoke all on table company_chat_key from public, anon, authenticated;
grant select, insert, update, delete on table company_chat_key to service_role;

-- Provision a key by hashing it outside Postgres, or with pgcrypto:
-- insert into company_chat_key(company_id, chat_api_key_hash, label)
-- values (1, encode(digest('replace-with-a-long-random-secret', 'sha256'), 'hex'), 'production');

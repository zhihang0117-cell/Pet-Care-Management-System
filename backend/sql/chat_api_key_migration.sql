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
-- up the presented key here — the same "derive tenant from a verified
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

create table if not exists company_chat_key (
  company_id bigint not null,
  chat_api_key text not null,
  label text,
  created_at timestamptz not null default now(),
  primary key (chat_api_key)
);

create index if not exists company_chat_key_company_id_idx
  on company_chat_key (company_id);

comment on table company_chat_key is
  'Maps a per-company X-Chat-Key (see main.py _resolve_chat_company) to the company_id /chat should operate as. Lets one Python deployment safely serve multiple companies.';

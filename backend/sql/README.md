# Manual Supabase migrations

None of these run automatically — there is no migration runner. Apply each
once in the Supabase SQL Editor. Every file uses `if not exists` / `or
replace`, so re-running any of them is safe.

`crud_consistency_functions.sql` and
`booking_conflict_prevention_migration.sql` both define the booking RPCs.
Run the conflict-prevention migration **last** so its locking/overlap checks
remain the active versions.

Status below was confirmed by read-only introspection against the live
project on 2026-08-02.

| File | Adds | Applied |
|---|---|---|
| `rls_policies.sql` | Multi-tenant RLS policies, `current_company_id()` | ✅ |
| `register_company_function.sql` | `register_company()` bootstrap RPC | ✅ |
| `crud_consistency_functions.sql` | Customer deletion plus base atomic booking create/update/delete RPCs | ✅ |
| `fix_missing_identity_columns.sql` | Auto-increment defaults on 12 tables | not verifiable via REST — assumed applied (see note below) |
| `company_settings_migration.sql` | `companies.settings_json` | ✅ |
| `company_availability_settings_migration.sql` | `company_closed_dates` | ✅ |
| `company_documents_migration.sql` | `company_documents` table (RAG source docs) | ✅ |
| `verify_payment_function.sql` | `payment.paid_at`, `payment.verified_by_staff_id` | ✅ |
| `payment_sla_migration.sql` | `payment.created_at` | ✅ |
| `crud_hardening_migration.sql` | `loyalty_points_adjustment`, `adjust_loyalty_points()`, `update_account_guarded()`, `delete_account_guarded()`, `cancel_approved_redemption()` | ✅ |
| `enquiry_refund_logo_migration.sql` | `companies.logo_path`, `messages.reply_text`, `payment.refund_reason`/`refunded_at`, `refund_payment()` | ✅ |
| `002_add_document_id_to_chunks_bge_large.sql` | `chunks_bge_large.document_id`, `replace_document_chunks_bge_large()` (restored — was deleted from the repo during the ai-backend -> app/ restructure, but was already applied live) | ✅ |
| `booking_conflict_prevention_migration.sql` | Atomic staff-overlap prevention, full-stay boarding room capacity/occupancy RPC, DAYCARE add-on persistence, boarding check-in/out event width (30 min) | ✅ conflict/occupancy base was verified live; **re-run after this release** — the DAYCARE add-on columns and the 10→30 minute boarding event width are new since the last verified apply |
| `redemption_rejection_reason_migration.sql` | `redemption.rejection_reason`, `decide_redemption()` reason parameter | not yet applied — created this session, no live verification available here |
| `chat_api_key_migration.sql` | `company_chat_key` table (per-company `/chat` auth, see `app/db/customer_context.py`) | not yet applied — created this session, no live verification available here |
| `staff_service_capability_migration.sql` | `staff.provides_service`, `staff.service_types_json` | not yet applied — created this session, no live verification available here |

`fix_missing_identity_columns.sql` sets column-level identity defaults, which
can't be confirmed with a `select` the way a missing column/table/function
can — every create/update path that depends on it (customers, pets, staff,
loyalty rules, leave, bookings, payment verification) is exercised by
`backend/test/*.test.js`, and those pass, so it's assumed applied.

`booking_conflict_prevention_migration.sql` uses half-open boarding intervals
`[check_in_date, check_out_date)`. A pet checked in on 1 Aug and checked out
on 10 Aug occupies the room on every date from 1–9 Aug; only 10 Aug becomes
available again. Staff check-in/check-out activity remains a separate,
short-lived constraint and must not be confused with room occupancy.

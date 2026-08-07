# Manual Supabase migrations

None of these run automatically — there is no migration runner. Apply them
in the documented order in the Supabase SQL Editor. Individual schema steps
are designed to be idempotent, but files that use `create or replace function`
must not be rerun out of order: an older RPC definition can overwrite a newer
conflict-protected version.

`crud_consistency_functions.sql` now keeps its legacy create/update bodies
under non-callable `_base` names. The production booking RPCs are defined only
by `booking_conflict_prevention_migration.sql`, so re-running the base file can
no longer remove locking/overlap checks.

For this repair, apply/re-run in this order:

1. `staff_service_capability_migration.sql`
2. `booking_data_integrity_migration.sql`
3. `booking_conflict_prevention_migration.sql`
4. `booking_slot_holds_migration.sql`
5. `loyalty_member_registration_migration.sql`
6. `leave_decision_atomic_migration.sql`
7. `verify_payment_function.sql`
8. `redemption_rejection_reason_migration.sql`
9. `security_integrity_hardening_migration.sql`
10. `cancel_booking_atomic_migration.sql`

RAG RPC ordering: apply `002_add_document_id_to_chunks_bge_large.sql` before
`shared_knowledge_base_rag_fix.sql`. Both repository definitions now preserve
`company_id IS NULL` as shared knowledge, so accidentally re-running the base
file no longer removes shared retrieval. The later file remains the canonical
review/audit migration for that behavior.

`verify_payment_function.sql` and
`redemption_rejection_reason_migration.sql` now both install only the latest
four-argument `decide_redemption(..., p_reason)` behavior and explicitly drop
the obsolete three-argument signature. They are safe against accidental
out-of-order re-runs without leaving ambiguous PostgREST overloads.

Status below was confirmed by read-only introspection against the live
project on 2026-08-07. “Re-run” means the repository version contains a
repair that was not visible in the live PostgREST schema cache.

| File | Adds | Applied |
|---|---|---|
| `rls_policies.sql` | Multi-tenant RLS policies, `current_company_id()` | ✅ |
| `register_company_function.sql` | `register_company()` bootstrap RPC | ✅ |
| `crud_consistency_functions.sql` | Customer deletion plus base atomic booking create/update/delete RPCs | ✅ |
| `fix_missing_identity_columns.sql` | Auto-increment defaults on 12 tables | not verifiable via REST — assumed applied (see note below) |
| `company_settings_migration.sql` | `companies.settings_json` | ✅ |
| `company_availability_settings_migration.sql` | `company_closed_dates`, `company_business_hours`, `replace_company_availability()` | ⚠️ tables exist, but the RPC was missing live — re-run |
| `company_documents_migration.sql` | `company_documents` table (RAG source docs) | ✅ |
| `verify_payment_function.sql` | `payment.paid_at`, `payment.verified_by_staff_id` | ✅ |
| `payment_sla_migration.sql` | `payment.created_at` | ✅ |
| `crud_hardening_migration.sql` | `loyalty_points_adjustment`, `adjust_loyalty_points()`, `update_account_guarded()`, `delete_account_guarded()`, `cancel_approved_redemption()` | ⚠️ functions exist, but `loyalty_points_adjustment` was absent from the live PostgREST schema cache — re-run |
| `enquiry_refund_logo_migration.sql` | `companies.logo_path`, `messages.reply_text`, `payment.refund_reason`/`refunded_at`, `refund_payment()` | ✅ |
| `002_add_document_id_to_chunks_bge_large.sql` | `chunks_bge_large.document_id`, `replace_document_chunks_bge_large()` (restored — was deleted from the repo during the ai-backend -> app/ restructure, but was already applied live) | ✅ |
| `booking_conflict_prevention_migration.sql` | Atomic staff-overlap prevention, full-stay boarding room capacity/occupancy RPC, DAYCARE add-on persistence, boarding check-in/out event width (30 min) | ✅ conflict/occupancy base was verified live; **re-run after this release** — the DAYCARE add-on columns and the 10→30 minute boarding event width are new since the last verified apply |
| `booking_data_integrity_migration.sql` | Decimal money columns, nonnegative amounts, valid active DAYCARE/BOARDING intervals | new — apply before re-running conflict prevention |
| `booking_slot_holds_migration.sql` | Shared 15-minute slot/room holds across multiple API workers | new — apply after conflict prevention |
| `loyalty_member_registration_migration.sql` | Unique customer membership plus atomic AI/dashboard registration RPC | new — resolve any reported legacy duplicates, then apply |
| `leave_decision_atomic_migration.sql` | Atomic leave decision; blocks approval until assigned active bookings are moved | new — apply after conflict prevention |
| `redemption_rejection_reason_migration.sql` | `redemption.rejection_reason`, `decide_redemption()` reason parameter | not yet applied — created this session, no live verification available here |
| `chat_api_key_migration.sql` | Hash-only `company_chat_key` table (per-company `/chat` auth, see `app/db/customer_context.py`) | not applied live |
| `staff_service_capability_migration.sql` | `staff.provides_service`, `staff.service_types_json` | not yet applied — created this session, no live verification available here |
| `security_integrity_hardening_migration.sql` | Protects chunks/backups, normalizes statuses, same-tenant FKs, availability RPC/schema refresh | new — apply after the prerequisites listed in `backend/README.md` |
| `backup_table_rls_hotfix.sql` | Targeted RLS + privilege lockdown for the actual `_status_backup_20260805` table | new — apply immediately; this avoids re-running unrelated hardening changes |
| `cancel_booking_atomic_migration.sql` | One transaction for booking cancellation + payment/refund/redemption reversal | new — apply last |

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

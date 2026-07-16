import { supabase } from "../supabaseClient.js";

/**
 * Which tables the LLM's generic list/get/create/update/delete tools may
 * touch, and what it may do to each. This is the main safety boundary for
 * "let an LLM CRUD the database":
 *
 * - Fully open tables (profile-ish data): full CRUD.
 * - Booking tables: update allowed (change status, reschedule, add notes,
 *   cancel) but NOT create/delete — bookings must be created through
 *   create_booking so the linked payment row & pricing stay correct.
 * - loyaltymember / redemption / payment: READ-ONLY for the LLM.
 *   Points, ledger rows, and payment status must only change through
 *   verify_payment (dedicated tool), which is the one place that keeps the
 *   member's balance and the redemption ledger consistent with each other.
 */
export const TABLE_ALLOWLIST = {
  customer: { idColumn: "customer_id", create: true, update: true, delete: true },
  pet: { idColumn: "pet_id", create: true, update: true, delete: true },
  staff: { idColumn: "staff_id", create: true, update: true, delete: true },
  coupon: { idColumn: "coupon_id", create: true, update: true, delete: true },
  leave: { idColumn: "leave_id", create: true, update: true, delete: false },
  messages: { idColumn: "message_id", create: true, update: false, delete: false },

  grooming_booking: { idColumn: "grooming_booking_id", create: false, update: true, delete: false },
  daycare_booking: { idColumn: "daycare_booking_id", create: false, update: true, delete: false },
  boarding_booking: { idColumn: "boarding_booking_id", create: false, update: true, delete: false },

  loyaltymember: { idColumn: "loyalty_id", create: false, update: false, delete: false },
  redemption: { idColumn: "redemption_id", create: false, update: false, delete: false },
  payment: { idColumn: "payment_id", create: false, update: false, delete: false },
};

function requireTable(table) {
  const config = TABLE_ALLOWLIST[table];
  if (!config) {
    const err = new Error(
      `Table "${table}" is not in the LLM allowlist. Available: ${Object.keys(TABLE_ALLOWLIST).join(", ")}`
    );
    err.status = 400;
    throw err;
  }
  return config;
}

export async function llmListRecords(companyId, { table, filters = {}, limit = 50 }) {
  requireTable(table);
  let query = supabase.from(table).select("*").eq("company_id", companyId);
  for (const [key, value] of Object.entries(filters)) {
    query = query.eq(key, value);
  }
  query = query.limit(Math.min(Number(limit) || 50, 200));
  const { data, error } = await query;
  if (error) throw error;
  return data;
}

export async function llmGetRecord(companyId, { table, id }) {
  const { idColumn } = requireTable(table);
  const { data, error } = await supabase.from(table).select("*").eq("company_id", companyId).eq(idColumn, id).single();
  if (error) {
    const err = new Error(`${table} ${id} not found`);
    err.status = 404;
    throw err;
  }
  return data;
}

export async function llmCreateRecord(companyId, { table, data }) {
  const { idColumn, create } = requireTable(table);
  if (!create) {
    const err = new Error(`Creating rows in "${table}" isn't allowed for the LLM. Use a dedicated tool instead (e.g. create_booking).`);
    err.status = 403;
    throw err;
  }
  const payload = { ...data, company_id: companyId };
  delete payload[idColumn];
  const { data: row, error } = await supabase.from(table).insert(payload).select().single();
  if (error) throw error;
  return row;
}

export async function llmUpdateRecord(companyId, { table, id, data }) {
  const { idColumn, update } = requireTable(table);
  if (!update) {
    const err = new Error(`Updating rows in "${table}" isn't allowed for the LLM. Use a dedicated tool instead (e.g. verify_payment).`);
    err.status = 403;
    throw err;
  }
  const payload = { ...data };
  delete payload[idColumn];
  delete payload.company_id;
  const { data: row, error } = await supabase
    .from(table)
    .update(payload)
    .eq("company_id", companyId)
    .eq(idColumn, id)
    .select()
    .single();
  if (error) throw error;
  return row;
}

export async function llmDeleteRecord(companyId, { table, id }) {
  const { idColumn, delete: canDelete } = requireTable(table);
  if (!canDelete) {
    const err = new Error(`Deleting rows in "${table}" isn't allowed for the LLM.`);
    err.status = 403;
    throw err;
  }
  const { error } = await supabase.from(table).delete().eq("company_id", companyId).eq(idColumn, id);
  if (error) throw error;
  return { deleted: true, table, id };
}

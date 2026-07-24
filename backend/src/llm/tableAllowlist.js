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
  customer: {
    idColumn: "customer_id", create: true, update: true, delete: true,
    fields: ["full_name", "phone_number", "address"],
  },
  pet: {
    idColumn: "pet_id", create: true, update: true, delete: true,
    fields: ["customer_id", "pet_name", "pet_type", "gender", "date_of_birth", "breed", "height_cm", "size",
      "vaccination_status", "vaccination_expired_date", "health_notes", "service_notes"],
  },
  staff: { idColumn: "staff_id", create: false, update: false, delete: false },
  coupon: { idColumn: "coupon_id", create: false, update: false, delete: false },
  leave: { idColumn: "leave_id", create: false, update: false, delete: false },
  messages: { idColumn: "message_id", create: false, update: false, delete: false },

  grooming_booking: { idColumn: "grooming_booking_id", create: false, update: false, delete: false },
  daycare_booking: { idColumn: "daycare_booking_id", create: false, update: false, delete: false },
  boarding_booking: { idColumn: "boarding_booking_id", create: false, update: false, delete: false },

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
  const numericLimit = Number(limit);
  if (!Number.isInteger(numericLimit) || numericLimit < 1 || numericLimit > 200) {
    const err = new Error("limit must be an integer from 1 to 200.");
    err.status = 400;
    throw err;
  }
  let query = supabase.from(table).select("*").eq("company_id", companyId);
  for (const [key, value] of Object.entries(filters)) {
    query = query.eq(key, value);
  }
  query = query.limit(numericLimit);
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
  const { idColumn, create, fields = [] } = requireTable(table);
  if (!create) {
    const err = new Error(`Creating rows in "${table}" isn't allowed for the LLM. Use a dedicated tool instead (e.g. create_booking).`);
    err.status = 403;
    throw err;
  }
  const payload = Object.fromEntries(Object.entries(data || {}).filter(([key]) => fields.includes(key)));
  payload.company_id = companyId;
  delete payload[idColumn];
  if (table === "customer" && (!String(payload.full_name || "").trim() || !String(payload.phone_number || "").trim() || !String(payload.address || "").trim())) {
    const err = new Error("Customer name, phone number, and address are required.");
    err.status = 400;
    throw err;
  }
  if (table === "pet") await validatePetPayload(companyId, payload, { creating: true });
  const { data: row, error } = await supabase.from(table).insert(payload).select().single();
  if (error) throw error;
  return row;
}

export async function llmUpdateRecord(companyId, { table, id, data }) {
  const { idColumn, update, fields = [] } = requireTable(table);
  if (!update) {
    const err = new Error(`Updating rows in "${table}" isn't allowed for the LLM. Use a dedicated tool instead (e.g. verify_payment).`);
    err.status = 403;
    throw err;
  }
  const payload = Object.fromEntries(Object.entries(data || {}).filter(([key]) => fields.includes(key)));
  delete payload[idColumn];
  delete payload.company_id;
  if (!Object.keys(payload).length) {
    const err = new Error("No supported fields were supplied.");
    err.status = 400;
    throw err;
  }
  if (table === "customer" && Object.values(payload).some(value => !String(value || "").trim())) {
    const err = new Error("Customer fields cannot be blank.");
    err.status = 400;
    throw err;
  }
  if (table === "pet") await validatePetPayload(companyId, payload);
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
  if (table === "customer") {
    const { error } = await supabase.rpc("delete_customer_with_pets", {
      p_company_id: companyId,
      p_customer_id: Number(id),
    });
    if (error) throw error;
    return { deleted: true, table, id };
  }
  const { error } = await supabase.from(table).delete().eq("company_id", companyId).eq(idColumn, id);
  if (error) throw error;
  return { deleted: true, table, id };
}

async function validatePetPayload(companyId, payload, { creating = false } = {}) {
  if (creating && (!payload.customer_id || !String(payload.pet_name || "").trim())) {
    const err = new Error("A valid owner and pet name are required.");
    err.status = 400;
    throw err;
  }
  if (payload.customer_id !== undefined) {
    const customerId = Number(payload.customer_id);
    const { data: owner } = await supabase.from("customer").select("customer_id")
      .eq("company_id", companyId).eq("customer_id", customerId).maybeSingle();
    if (!owner) {
      const err = new Error("Owner not found for this company.");
      err.status = 404;
      throw err;
    }
    payload.customer_id = customerId;
  }
  if (payload.pet_name !== undefined && !String(payload.pet_name).trim()) {
    const err = new Error("Pet name cannot be blank.");
    err.status = 400;
    throw err;
  }
}

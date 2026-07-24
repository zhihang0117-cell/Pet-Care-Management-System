import { makeCrudRouter } from "../lib/crudFactory.js";
import { supabase } from "../supabaseClient.js";

async function petPayload(req, { creating = false } = {}) {
  const payload = {};
  const hasCustomer = req.body.customer_id !== undefined;
  const customerId = hasCustomer ? Number(req.body.customer_id) : null;
  const hasName = req.body.pet_name !== undefined;
  const petName = hasName ? String(req.body.pet_name || "").trim() : null;
  if ((creating && (!hasCustomer || !hasName)) ||
      (hasCustomer && (!Number.isInteger(customerId) || customerId <= 0)) ||
      (hasName && !petName)) {
    const error = new Error("A valid owner and pet name are required.");
    error.status = 400;
    throw error;
  }
  const hasHeight = req.body.height_cm !== undefined;
  const height = !hasHeight || req.body.height_cm == null || req.body.height_cm === "" ? null : Number(req.body.height_cm);
  if (height !== null && (!Number.isFinite(height) || height < 0)) {
    const error = new Error("height_cm must be zero or greater.");
    error.status = 400;
    throw error;
  }
  if (hasCustomer) {
    const { data: owner } = await supabase.from("customer").select("customer_id")
      .eq("company_id", req.companyId).eq("customer_id", customerId).maybeSingle();
    if (!owner) {
      const error = new Error("Owner not found for this company.");
      error.status = 404;
      throw error;
    }
    payload.customer_id = customerId;
  }
  if (hasName) payload.pet_name = petName;
  for (const key of ["pet_type", "gender", "breed", "size", "vaccination_status", "health_notes", "service_notes"]) {
    if (req.body[key] !== undefined) payload[key] = String(req.body[key] || "").trim() || null;
  }
  for (const key of ["date_of_birth", "vaccination_expired_date"]) {
    if (req.body[key] !== undefined) payload[key] = req.body[key] || null;
  }
  if (hasHeight) payload.height_cm = height;
  if (!creating && Object.keys(payload).length === 0) {
    const error = new Error("Nothing to update.");
    error.status = 400;
    throw error;
  }
  return payload;
}

export const petsRouter = makeCrudRouter({
  table: "pet",
  idColumn: "pet_id",
  searchableColumns: ["pet_name", "breed", "pet_type"],
  defaultOrder: { column: "pet_id", ascending: true },
  createPayload: req => petPayload(req, { creating: true }),
  updatePayload: petPayload,
  filterColumns: ["customer_id", "pet_type", "vaccination_status"],
});

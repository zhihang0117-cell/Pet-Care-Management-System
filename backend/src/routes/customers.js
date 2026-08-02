import { makeCrudRouter } from "../lib/crudFactory.js";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

function customerPayload(req, { creating = false } = {}) {
  const payload = {};
  for (const [key, source] of [["full_name", "full_name"], ["phone_number", "phone_number"], ["address", "address"]]) {
    if (req.body[source] !== undefined) payload[key] = String(req.body[source] || "").trim();
  }
  if ((creating && Object.keys(payload).length !== 3) || Object.values(payload).some(value => !value)) {
    const error = new Error("Customer name, phone number, and address are required.");
    error.status = 400;
    throw error;
  }
  if (payload.phone_number !== undefined) {
    const raw = payload.phone_number;
    const digits = raw.replace(/\D/g, "");
    if (!/^\+?[0-9][0-9\s().-]*$/.test(raw) || digits.length < 8 || digits.length > 15) {
      const error = new Error("Phone number must contain between 8 and 15 digits.");
      error.status = 400;
      throw error;
    }
  }
  if (!creating && Object.keys(payload).length === 0) {
    const error = new Error("Nothing to update.");
    error.status = 400;
    throw error;
  }
  return payload;
}

export const customersRouter = makeCrudRouter({
  table: "customer",
  idColumn: "customer_id",
  searchableColumns: ["full_name", "phone_number", "address"],
  defaultOrder: { column: "customer_id", ascending: true },
  createPayload: req => customerPayload(req, { creating: true }),
  updatePayload: customerPayload,
  filterColumns: [],
});

// Deletes the customer and linked pets in one database transaction. Any
// booking/loyalty foreign-key dependency aborts the whole operation.
customersRouter.delete(
  "/:id/with-pets",
  asyncHandler(async (req, res) => {
    const { error } = await supabase.rpc("delete_customer_with_pets", {
      p_company_id: req.companyId,
      p_customer_id: Number(req.params.id),
    });
    if (error) return res.status(400).json({ error: error.message });
    res.status(204).end();
  })
);

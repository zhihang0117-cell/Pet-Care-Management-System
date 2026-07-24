import { makeCrudRouter } from "../lib/crudFactory.js";
import { requireManager } from "../middleware/authUser.js";

function staffPayload(req, { creating = false } = {}) {
  const payload = {};
  if (req.body.staff_name !== undefined) payload.staff_name = String(req.body.staff_name || "").trim();
  if (req.body.role !== undefined) payload.role = String(req.body.role || "").trim();
  if (req.body.status !== undefined || creating) payload.status = String(req.body.status || "active").trim().toLowerCase();
  if (req.body.email !== undefined) payload.email = req.body.email == null || req.body.email === "" ? null : String(req.body.email).trim().toLowerCase();
  if (req.body.phone !== undefined) payload.phone = req.body.phone == null || req.body.phone === "" ? null : String(req.body.phone).trim();
  if (req.body.off_days_json !== undefined) payload.off_days_json = Array.isArray(req.body.off_days_json) ? req.body.off_days_json : [];
  if ((creating && (!payload.staff_name || !payload.role)) ||
      (payload.staff_name !== undefined && !payload.staff_name) ||
      (payload.role !== undefined && !["Manager", "Staff"].includes(payload.role))) {
    const error = new Error("Staff name and a valid role are required.");
    error.status = 400;
    throw error;
  }
  if (payload.status !== undefined && !["active", "inactive"].includes(payload.status)) {
    const error = new Error("status must be active or inactive.");
    error.status = 400;
    throw error;
  }
  if (!creating && Object.keys(payload).length === 0) {
    const error = new Error("Nothing to update.");
    error.status = 400;
    throw error;
  }
  return payload;
}

export const staffRouter = makeCrudRouter({
  table: "staff",
  idColumn: "staff_id",
  searchableColumns: ["staff_name", "role", "email"],
  defaultOrder: { column: "staff_id", ascending: true },
  createMiddleware: [requireManager],
  updateMiddleware: [requireManager],
  deleteMiddleware: [requireManager],
  createPayload: req => staffPayload(req, { creating: true }),
  updatePayload: staffPayload,
  filterColumns: ["role", "status"],
});

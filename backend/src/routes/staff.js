import { makeCrudRouter } from "../lib/crudFactory.js";
import { requireManager } from "../middleware/authUser.js";

const SERVICE_TYPES = new Set(["GROOMING", "DAYCARE", "BOARDING"]);

function staffPayload(req, { creating = false } = {}) {
  const payload = {};
  if (req.body.staff_name !== undefined) payload.staff_name = String(req.body.staff_name || "").trim();
  if (req.body.role !== undefined) payload.role = String(req.body.role || "").trim();
  if (req.body.status !== undefined || creating) payload.status = String(req.body.status || "active").trim().toLowerCase();
  if (req.body.email !== undefined) payload.email = req.body.email == null || req.body.email === "" ? null : String(req.body.email).trim().toLowerCase();
  if (req.body.phone !== undefined) payload.phone = req.body.phone == null || req.body.phone === "" ? null : String(req.body.phone).trim();
  if (req.body.off_days_json !== undefined) payload.off_days_json = Array.isArray(req.body.off_days_json) ? req.body.off_days_json : [];
  // provides_service: whether this staff member performs bookable services
  // at all (false for e.g. reception/admin-only staff) — defaults true on
  // create so a manager has to deliberately opt someone OUT, matching the
  // migration's own column default for existing rows.
  if (req.body.provides_service !== undefined || creating) {
    payload.provides_service = creating && req.body.provides_service === undefined
      ? true
      : Boolean(req.body.provides_service);
  }
  if (req.body.service_types_json !== undefined) {
    const types = Array.isArray(req.body.service_types_json)
      ? [...new Set(req.body.service_types_json.map(t => String(t).trim().toUpperCase()))]
      : [];
    if (types.some(t => !SERVICE_TYPES.has(t))) {
      const error = new Error("service_types_json may only contain GROOMING, DAYCARE, or BOARDING.");
      error.status = 400;
      throw error;
    }
    payload.service_types_json = types;
  } else if (creating) {
    payload.service_types_json = ["GROOMING", "DAYCARE", "BOARDING"];
  }
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

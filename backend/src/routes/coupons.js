import { makeCrudRouter } from "../lib/crudFactory.js";
import { requireManager } from "../middleware/authUser.js";

const REWARD_TYPES = new Set(["Discount (RM value)", "Free service"]);

function validateCouponPayload(body, { creating = false } = {}) {
  const payload = {};
  for (const key of ["points_required", "reward_name", "reward_type", "discount_value (RM)", "expiry_date"]) {
    if (body[key] !== undefined) payload[key] = body[key];
  }

  if (creating && (!payload.reward_name || payload.points_required === undefined || !payload.reward_type)) {
    const err = new Error("points_required, reward_name, and reward_type are required.");
    err.status = 400;
    throw err;
  }
  if (payload.points_required !== undefined) {
    const points = Number(payload.points_required);
    if (!Number.isInteger(points) || points < 1) {
      const err = new Error("points_required must be a positive whole number.");
      err.status = 400;
      throw err;
    }
    payload.points_required = points;
  }
  if (payload.reward_name !== undefined) {
    payload.reward_name = String(payload.reward_name).trim();
    if (!payload.reward_name) {
      const err = new Error("reward_name cannot be blank.");
      err.status = 400;
      throw err;
    }
  }
  if (payload.reward_type !== undefined && !REWARD_TYPES.has(payload.reward_type)) {
    const err = new Error("reward_type must be 'Discount (RM value)' or 'Free service'.");
    err.status = 400;
    throw err;
  }
  if (payload["discount_value (RM)"] !== undefined) {
    const discount = Number(payload["discount_value (RM)"]);
    if (!Number.isFinite(discount) || discount < 0) {
      const err = new Error("discount value must be zero or greater.");
      err.status = 400;
      throw err;
    }
    payload["discount_value (RM)"] = String(discount);
  }
  if (payload.expiry_date === "") payload.expiry_date = null;
  if (payload.expiry_date != null && !/^\d{4}-\d{2}-\d{2}$/.test(String(payload.expiry_date))) {
    const err = new Error("expiry_date must use YYYY-MM-DD format.");
    err.status = 400;
    throw err;
  }
  return payload;
}

export const couponsRouter = makeCrudRouter({
  table: "coupon",
  idColumn: "coupon_id",
  searchableColumns: ["reward_name", "reward_type"],
  defaultOrder: { column: "points_required", ascending: true },
  createMiddleware: [requireManager],
  updateMiddleware: [requireManager],
  deleteMiddleware: [requireManager],
  createPayload: (req) => validateCouponPayload(req.body, { creating: true }),
  updatePayload: (req) => validateCouponPayload(req.body),
});

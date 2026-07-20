import "dotenv/config";

export const EARN_RATE = Number(process.env.LOYALTY_EARN_RATE || 1);

// Matches the tiers already used in the frontend (common.js LOYALTY_TIERS),
// kept here so the backend and frontend agree on tier boundaries.
export const LOYALTY_TIERS = [
  { min: 1200, name: "Platinum" },
  { min: 700, name: "Gold" },
  { min: 300, name: "Silver" },
  { min: 0, name: "Bronze" },
];

export function getLoyaltyTier(points) {
  return LOYALTY_TIERS.find((tier) => points >= tier.min).name;
}

/**
 * Computes the final payable amount for a booking/payment.
 *
 * @param {number} basePrice        - the service's base price (RM)
 * @param {number} addOnPrice       - add-on price, 0 if none
 * @param {object} [coupon]         - a row from coupon, or null/undefined if no voucher applied
 *   coupon.reward_type is either "Discount (RM value)" or "Free service"
 *   coupon["discount_value (RM)"] holds the RM amount for discount-type coupons
 * @returns {{ finalAmount: number, discountApplied: number, breakdown: object }}
 */
export function computeFinalAmount(basePrice, addOnPrice, coupon) {
  const base = Number(basePrice) || 0;
  const addOn = Number(addOnPrice) || 0;
  const subtotal = base + addOn;

  let discountApplied = 0;

  if (coupon) {
    if (coupon.reward_type === "Free service") {
      // Free service voucher waives the whole subtotal for this booking.
      discountApplied = subtotal;
    } else {
      // "Discount (RM value)" — flat RM amount off, never below zero.
      const rawDiscount = Number(coupon["discount_value (RM)"]) || 0;
      discountApplied = Math.min(rawDiscount, subtotal);
    }
  }

  const finalAmount = Math.max(0, subtotal - discountApplied);

  return {
    finalAmount,
    discountApplied,
    breakdown: { basePrice: base, addOnPrice: addOn, subtotal, discountApplied, finalAmount },
  };
}

/**
 * Validates that a member has enough points to redeem a given coupon.
 * Throws a plain Error with a `.status` (HTTP code) if invalid, so route
 * handlers can just catch and forward it.
 */
export function assertCanRedeem(member, coupon) {
  if (!member) {
    const err = new Error("No loyalty member found for this customer.");
    err.status = 404;
    throw err;
  }
  if (!coupon) {
    const err = new Error("Coupon/voucher not found.");
    err.status = 404;
    throw err;
  }
  const expiryDate = String(coupon.expiry_date || "").slice(0, 10);
  const today = new Date().toISOString().slice(0, 10);
  if (expiryDate && expiryDate < today) {
    const err = new Error("This voucher has expired.");
    err.status = 400;
    throw err;
  }
  if (member.points_balance < coupon.points_required) {
    const err = new Error(
      `Not enough points. This voucher needs ${coupon.points_required} points, ` +
        `member only has ${member.points_balance}.`
    );
    err.status = 400;
    throw err;
  }
}

export function pointsEarnedFor(finalAmount) {
  return Math.round(Number(finalAmount) * EARN_RATE);
}

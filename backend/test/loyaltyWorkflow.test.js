import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { assertCanRedeem, computeFinalAmount } from "../src/lib/pricing.js";

test("voucher validation accepts sufficient points and rejects invalid balances", () => {
  const coupon = {
    points_required: 300,
    reward_type: "Discount (RM value)",
    "discount_value (RM)": "20",
    expiry_date: null,
  };

  assert.doesNotThrow(() => assertCanRedeem({ points_balance: 300 }, coupon));
  assert.throws(
    () => assertCanRedeem({ points_balance: 299 }, coupon),
    /needs 300 points, member only has 299/,
  );
  assert.throws(
    () => assertCanRedeem({ points_balance: "not-a-number" }, coupon),
    /points balance is invalid/,
  );
  assert.throws(
    () => assertCanRedeem({ points_balance: 500 }, { ...coupon, points_required: 0 }),
    /points requirement is invalid/,
  );
});

test("voucher amount math never makes the payable total negative", () => {
  assert.deepEqual(
    computeFinalAmount(100, 25, {
      reward_type: "Discount (RM value)",
      "discount_value (RM)": 30,
    }),
    {
      finalAmount: 95,
      discountApplied: 30,
      breakdown: {
        basePrice: 100,
        addOnPrice: 25,
        subtotal: 125,
        discountApplied: 30,
        finalAmount: 95,
      },
    },
  );
  assert.equal(
    computeFinalAmount(50, 0, {
      reward_type: "Discount (RM value)",
      "discount_value (RM)": 999,
    }).finalAmount,
    0,
  );
  assert.equal(
    computeFinalAmount(80, 20, { reward_type: "Free service" }).finalAmount,
    0,
  );
});

test("approval workflow locks rows and payment verification requires approval", async () => {
  const sql = await readFile(new URL("../sql/verify_payment_function.sql", import.meta.url), "utf8");
  const paymentService = await readFile(new URL("../src/lib/paymentService.js", import.meta.url), "utf8");
  const redemptionRoute = await readFile(new URL("../src/routes/redemptions.js", import.meta.url), "utf8");
  const frontend = await readFile(new URL("../../web/common.js", import.meta.url), "utf8");

  assert.match(sql, /create or replace function request_redemption/);
  assert.match(sql, /create or replace function decide_redemption/);
  assert.match(sql, /from loyaltymember[\s\S]*for update/);
  assert.match(sql, /v_new_balance := v_member\.points_balance - v_spend/);
  assert.match(sql, /v_new_balance := v_member\.points_balance \+ v_earn/);
  assert.match(sql, /No approved point redemption for this payment/);
  assert.match(paymentService, /No approved point redemption for this payment/);
  assert.match(redemptionRoute, /"\/:id\/decision"/);
  assert.match(frontend, />Approve<\/button>/);
  assert.match(frontend, />Reject<\/button>/);
  assert.match(frontend, /No approved point redemption/);
});

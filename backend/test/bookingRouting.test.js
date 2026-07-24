import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { BOOKING_TYPES, getBookingTypeConfig } from "../src/lib/bookingTypes.js";

const EXPECTED_ROUTES = {
  grooming: { table: "grooming_booking", idColumn: "grooming_booking_id", basePriceCol: "price" },
  daycare: { table: "daycare_booking", idColumn: "daycare_booking_id", basePriceCol: "price" },
  boarding: { table: "boarding_booking", idColumn: "boarding_booking_id", basePriceCol: "total_price" },
};

test("each service writes to its matching booking table", () => {
  assert.deepEqual(Object.keys(BOOKING_TYPES).sort(), Object.keys(EXPECTED_ROUTES).sort());

  for (const [type, expected] of Object.entries(EXPECTED_ROUTES)) {
    const config = getBookingTypeConfig(type);
    assert.equal(config.table, expected.table, `${type} must use ${expected.table}`);
    assert.equal(config.idColumn, expected.idColumn, `${type} must use ${expected.idColumn}`);
    assert.equal(config.basePriceCol, expected.basePriceCol, `${type} uses the wrong price column`);
    assert.equal(config.paymentIdColumn, "payment_id");
  }
});

test("unknown service types never resolve to a booking table", () => {
  assert.equal(getBookingTypeConfig("general"), null);
  assert.equal(getBookingTypeConfig(""), null);
  assert.equal(getBookingTypeConfig("grooming_booking"), null);
});

test("booking routing configuration cannot be mutated at runtime", () => {
  assert.equal(Object.isFrozen(BOOKING_TYPES), true);
  assert.equal(Object.isFrozen(BOOKING_TYPES.grooming), true);
  assert.throws(() => { BOOKING_TYPES.grooming.table = "boarding_booking"; }, TypeError);
});

test("booking, payment, and redemption statuses remain independent", async () => {
  const bookingService = await readFile(new URL("../src/lib/bookingService.js", import.meta.url), "utf8");
  const paymentService = await readFile(new URL("../src/lib/paymentService.js", import.meta.url), "utf8");
  const verificationSql = await readFile(new URL("../sql/verify_payment_function.sql", import.meta.url), "utf8");
  const updateBookingSource = bookingService.slice(
    bookingService.indexOf("export async function updateBooking"),
    bookingService.indexOf("export async function deleteBooking"),
  );

  assert.doesNotMatch(updateBookingSource, /paymentUpdate\.status\s*=/);
  assert.doesNotMatch(updateBookingSource, /Verify the linked payment before marking this booking Done/);
  assert.doesNotMatch(updateBookingSource, /A paid booking must remain Done/);
  assert.match(updateBookingSource, /if \(pricingChanged\) \{[\s\S]*?\.from\("payment"\)/);
  assert.doesNotMatch(updateBookingSource, /\.from\("payment"\)[\s\S]*?const pricingChanged/);
  assert.doesNotMatch(paymentService, /booking_status === "Cancelled"/);
  assert.doesNotMatch(verificationSql, /update\s+(grooming|daycare|boarding)_booking\s+set\s+booking_status/i);
});

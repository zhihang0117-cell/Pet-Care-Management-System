import test from "node:test";
import assert from "node:assert/strict";

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

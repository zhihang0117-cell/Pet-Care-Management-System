export const BOOKING_TYPES = Object.freeze({
  grooming: Object.freeze({
    table: "grooming_booking",
    idColumn: "grooming_booking_id",
    hasAddOn: true,
    basePriceCol: "price",
    addOnPriceCol: "add_on_price",
    paymentIdColumn: "payment_id",
  }),
  daycare: Object.freeze({
    table: "daycare_booking",
    idColumn: "daycare_booking_id",
    hasAddOn: false,
    basePriceCol: "price",
    addOnPriceCol: null,
    paymentIdColumn: "payment_id",
  }),
  boarding: Object.freeze({
    table: "boarding_booking",
    idColumn: "boarding_booking_id",
    hasAddOn: false,
    basePriceCol: "total_price",
    addOnPriceCol: null,
    multiNight: true,
    paymentIdColumn: "payment_id",
  }),
});

export function getBookingTypeConfig(type) {
  return BOOKING_TYPES[type] || null;
}

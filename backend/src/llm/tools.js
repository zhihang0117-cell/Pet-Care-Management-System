import { supabase } from "../supabaseClient.js";
import { createBooking } from "../lib/bookingService.js";
import { getPaymentDetail, quoteVoucher, verifyPayment } from "../lib/paymentService.js";
import {
  TABLE_ALLOWLIST,
  llmListRecords,
  llmGetRecord,
  llmCreateRecord,
  llmUpdateRecord,
  llmDeleteRecord,
} from "./tableAllowlist.js";

const tableNames = Object.keys(TABLE_ALLOWLIST);

// Anthropic-style tool definitions (name / description / input_schema).
// If you're wiring this to OpenAI-style function calling instead, the same
// objects work with a trivial reshape (input_schema -> parameters).
export const TOOL_SCHEMA = [
  {
    name: "list_records",
    description: `List rows from one of the allowed tables, optionally filtered by exact-match column values. Allowed tables: ${tableNames.join(", ")}.`,
    input_schema: {
      type: "object",
      properties: {
        table: { type: "string", enum: tableNames },
        filters: { type: "object", description: "column:value pairs for exact-match filtering, e.g. { booking_status: 'Pending' }" },
        limit: { type: "number", description: "max rows to return, default 50, max 200" },
      },
      required: ["table"],
    },
  },
  {
    name: "get_record",
    description: "Get a single row from an allowed table by its primary key.",
    input_schema: {
      type: "object",
      properties: { table: { type: "string", enum: tableNames }, id: { type: ["string", "number"] } },
      required: ["table", "id"],
    },
  },
  {
    name: "create_record",
    description: "Create a row in an allowed table (not permitted for booking/payment/member/redemption tables — use the dedicated tools for those).",
    input_schema: {
      type: "object",
      properties: { table: { type: "string", enum: tableNames }, data: { type: "object" } },
      required: ["table", "data"],
    },
  },
  {
    name: "update_record",
    description: "Update a row in an allowed table by primary key (e.g. change a booking's status, edit a customer's phone number).",
    input_schema: {
      type: "object",
      properties: {
        table: { type: "string", enum: tableNames },
        id: { type: ["string", "number"] },
        data: { type: "object" },
      },
      required: ["table", "id", "data"],
    },
  },
  {
    name: "delete_record",
    description: "Delete a row in an allowed table by primary key.",
    input_schema: {
      type: "object",
      properties: { table: { type: "string", enum: tableNames }, id: { type: ["string", "number"] } },
      required: ["table", "id"],
    },
  },
  {
    name: "create_booking",
    description:
      "Create a grooming, daycare, or boarding booking. Automatically creates the linked payment record with the base price + add-on price computed for you. Does not apply a voucher — that happens later at verify_payment time.",
    input_schema: {
      type: "object",
      properties: {
        type: { type: "string", enum: ["grooming", "daycare", "boarding"] },
        pet_id: { type: "number" },
        staff_id: { type: "number" },
        service_name: { type: "string", description: "grooming only" },
        booking_date: { type: "string", description: "grooming/daycare, format YYYY-MM-DD" },
        booking_time: { type: "string", description: "grooming only, HH:MM:SS" },
        price: { type: "number", description: "grooming/daycare base price" },
        add_on: { type: "string", description: "grooming only" },
        add_on_price: { type: "number", description: "grooming only" },
        check_in_time: { type: "string" },
        check_out_time: { type: "string" },
        package_type: { type: "string", description: "daycare only" },
        special_instruction: { type: "string", description: "daycare only" },
        check_in_date: { type: "string", description: "boarding only, format YYYY-MM-DD" },
        check_out_date: { type: "string", description: "boarding only, format YYYY-MM-DD" },
        room_type: { type: "string", description: "boarding only" },
        price_per_night: { type: "number", description: "boarding only" },
        feeding_instruction: { type: "string", description: "boarding only" },
        medical_instruction: { type: "string", description: "boarding only" },
        notes: { type: "string" },
      },
      required: ["type", "pet_id", "staff_id"],
    },
  },
  {
    name: "get_payment_detail",
    description: "Get a payment plus the booking it's for and the customer's loyalty member info — useful before deciding whether/what voucher to apply.",
    input_schema: { type: "object", properties: { payment_id: { type: "number" } }, required: ["payment_id"] },
  },
  {
    name: "quote_voucher",
    description: "Preview the final amount if a given coupon were applied to a payment, and whether the member has enough points. Does not change anything.",
    input_schema: {
      type: "object",
      properties: { payment_id: { type: "number" }, coupon_id: { type: "number" } },
      required: ["payment_id"],
    },
  },
  {
    name: "verify_payment",
    description:
      "Verify a payment (and optionally redeem a voucher on it) in one atomic step: deducts/earns loyalty points, logs the redemption, marks the payment Paid with a paid_at timestamp, and marks the booking Done.",
    input_schema: {
      type: "object",
      properties: {
        payment_id: { type: "number" },
        coupon_id: { type: "number", description: "omit if no voucher is being redeemed" },
        staff_id: { type: "number", description: "who verified it" },
      },
      required: ["payment_id"],
    },
  },
  {
    name: "decide_leave_request",
    description: "Approve or reject a staff leave request.",
    input_schema: {
      type: "object",
      properties: {
        leave_id: { type: "number" },
        status: { type: "string", enum: ["Approved", "Rejected"] },
        reviewed_by_staff_id: { type: "number" },
      },
      required: ["leave_id", "status"],
    },
  },
];

function todayStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: d.toTimeString().slice(0, 8) };
}

/** Executes one named tool call. Throws (with .status) on failure. */
export async function executeTool(companyId, toolName, input = {}) {
  switch (toolName) {
    case "list_records":
      return llmListRecords(companyId, input);
    case "get_record":
      return llmGetRecord(companyId, input);
    case "create_record":
      return llmCreateRecord(companyId, input);
    case "update_record":
      return llmUpdateRecord(companyId, input);
    case "delete_record":
      return llmDeleteRecord(companyId, input);

    case "create_booking":
      return createBooking(input.type, companyId, input);

    case "get_payment_detail":
      return getPaymentDetail(companyId, input.payment_id);

    case "quote_voucher":
      return quoteVoucher(companyId, input.payment_id, input.coupon_id);

    case "verify_payment":
      return verifyPayment({
        companyId,
        paymentId: input.payment_id,
        couponId: input.coupon_id || null,
        staffId: input.staff_id || null,
      });

    case "decide_leave_request": {
      if (!["Approved", "Rejected"].includes(input.status)) {
        const err = new Error("status must be 'Approved' or 'Rejected'.");
        err.status = 400;
        throw err;
      }
      const { date, time } = todayStamp();
      const { data, error } = await supabase
        .from("leave")
        .update({
          status: input.status,
          reviewed_by_staff_id: input.reviewed_by_staff_id ?? null,
          reviewed_date: date,
          reviewed_time: time,
        })
        .eq("company_id", companyId)
        .eq("leave_id", input.leave_id)
        .select()
        .single();
      if (error) throw error;
      return data;
    }

    default: {
      const err = new Error(`Unknown tool "${toolName}".`);
      err.status = 400;
      throw err;
    }
  }
}

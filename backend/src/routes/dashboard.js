import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

export const dashboardRouter = Router();

const BOOKING_TABLES = [
  { type: "grooming", table: "grooming_booking" },
  { type: "daycare", table: "daycare_booking" },
  { type: "boarding", table: "boarding_booking" },
];

function todayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// NOTE ON DATES: your date/time columns are real Postgres `date`/`time`
// columns, not text — so as long as values are passed as zero-padded ISO
// strings (YYYY-MM-DD), both exact-match filters (the /summary endpoint
// below) and range filters (">= start AND <= end", used by /revenue below)
// are safe to do directly in SQL/PostgREST. No JS-side date comparison
// workaround needed.

async function countByStatus(table, companyId, statusColumn = "booking_status") {
  const { data, error } = await supabase.from(table).select(statusColumn).eq("company_id", companyId);
  if (error) throw error;
  const counts = {};
  for (const row of data) {
    const key = row[statusColumn] || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  }
  counts._total = data.length;
  return counts;
}

/**
 * GET /api/dashboard/summary
 * One-shot payload for KPI cards: booking status breakdown per service,
 * today's booking count per service, pending payments, pending loyalty
 * redemptions, and pending leave requests.
 */
dashboardRouter.get(
  "/summary",
  asyncHandler(async (req, res) => {
    const companyId = req.companyId;
    const today = todayStr();

    const bookingSummary = {};
    let todayBookingsTotal = 0;

    for (const { type, table } of BOOKING_TABLES) {
      bookingSummary[type] = await countByStatus(table, companyId);

      const dateColumn = type === "boarding" ? "check_in_date" : "booking_date";
      const { count, error } = await supabase
        .from(table)
        .select("*", { count: "exact", head: true })
        .eq("company_id", companyId)
        .eq(dateColumn, today);
      if (error) throw error;
      bookingSummary[type]._today = count || 0;
      todayBookingsTotal += count || 0;
    }

    const [pendingPaymentsResult, loyaltyLedgerResult, pendingLeaveResult] = await Promise.all([
      supabase.from("payment").select("*", { count: "exact", head: true }).eq("company_id", companyId).eq("status", "Pending"),
      // The redemption table is a historical ledger (rows are only ever created by
      // verify_payment), so this count is "total loyalty transactions so far", not
      // a pending queue — "pending approval" for loyalty lives on payment.status.
      supabase.from("redemption").select("*", { count: "exact", head: true }).eq("company_id", companyId),
      supabase.from("leave").select("*", { count: "exact", head: true }).eq("company_id", companyId).eq("status", "Pending"),
    ]);

    res.json({
      date: today,
      bookings: bookingSummary,
      todayBookingsTotal,
      pendingPayments: pendingPaymentsResult.count || 0,
      loyaltyLedgerRows: loyaltyLedgerResult.count || 0,
      pendingLeaveRequests: pendingLeaveResult.count || 0,
    });
  })
);

/**
 * GET /api/dashboard/revenue?period=today|week|month
 * Sums payment.final_amount for status='Paid' payments whose `date`
 * falls in the period, filtered directly in SQL (see date note above).
 */
dashboardRouter.get(
  "/revenue",
  asyncHandler(async (req, res) => {
    const period = req.query.period || "today";
    const companyId = req.companyId;
    const pad = (n) => String(n).padStart(2, "0");
    const toIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

    const now = new Date();
    let start;
    if (period === "today") {
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    } else if (period === "week") {
      start = new Date(now);
      start.setDate(start.getDate() - 7);
    } else {
      start = new Date(now.getFullYear(), now.getMonth(), 1);
    }

    const { data, error } = await supabase
      .from("payment")
      .select("payment_id, final_amount, date, status")
      .eq("company_id", companyId)
      .eq("status", "Paid")
      .gte("date", toIso(start))
      .lte("date", toIso(now));
    if (error) return res.status(400).json({ error: error.message });

    const totalRevenue = data.reduce((sum, row) => sum + Number(row.final_amount || 0), 0);

    res.json({
      period,
      from: toIso(start),
      to: toIso(now),
      paymentCount: data.length,
      totalRevenue,
    });
  })
);

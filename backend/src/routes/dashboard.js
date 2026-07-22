import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

export const dashboardRouter = Router();

const BOOKING_TABLES = [
  { type: "grooming", table: "grooming_booking" },
  { type: "daycare", table: "daycare_booking" },
  { type: "boarding", table: "boarding_booking" },
];

async function enabledBookingTables(companyId) {
  const { data, error } = await supabase
    .from("companies")
    .select("settings_json")
    .eq("company_id", companyId)
    .single();
  if (error) throw error;
  const configured = data?.settings_json?.selected_services;
  if (!Array.isArray(configured) || configured.length === 0) return BOOKING_TABLES;
  const enabled = new Set(configured);
  return BOOKING_TABLES.filter(({ type }) => enabled.has(type));
}

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
    const today = req.query.date || todayStr();

    const bookingSummary = {};
    let todayBookingsTotal = 0;
    const companyBookingTables = await enabledBookingTables(companyId);

    for (const { type, table } of companyBookingTables) {
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
      supabase.from("payment").select("*", { count: "exact", head: true }).eq("company_id", companyId).in("status", ["Pending", "Unpaid"]),
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
 * GET /api/dashboard/revenue?period=today|week|month&anchor=YYYY-MM-DD
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

    const anchor = req.query.anchor || todayStr();
    const anchorDate = new Date(`${anchor}T00:00:00`);
    let start;
    let end;
    if (period === "today") {
      start = new Date(anchorDate);
      end = new Date(anchorDate);
    } else if (period === "week") {
      const diffToMonday = (anchorDate.getDay() + 6) % 7;
      start = new Date(anchorDate);
      start.setDate(anchorDate.getDate() - diffToMonday);
      end = new Date(start);
      end.setDate(start.getDate() + 6);
    } else {
      start = new Date(anchorDate.getFullYear(), anchorDate.getMonth(), 1);
      end = new Date(anchorDate.getFullYear(), anchorDate.getMonth() + 1, 0);
    }

    const { data, error } = await supabase
      .from("payment")
      .select("payment_id, final_amount, date, status")
      .eq("company_id", companyId)
      .eq("status", "Paid")
      .gte("date", toIso(start))
      .lte("date", toIso(end));
    if (error) return res.status(400).json({ error: error.message });

    const totalRevenue = data.reduce((sum, row) => sum + Number(row.final_amount || 0), 0);

    res.json({
      period,
      from: toIso(start),
      to: toIso(end),
      paymentCount: data.length,
      totalRevenue,
    });
  })
);

/**
 * GET /api/dashboard/trend?period=weekly|monthly&anchor=YYYY-MM-DD
 * Buckets real bookings (all 3 tables) + paid payments within the anchor's
 * week (daily buckets) or month (weekly buckets) — powers dashboard.html's
 * trend charts and prev/next period navigation. Fetches each resource once
 * for the whole range and buckets in Node, rather than one query per bucket.
 */
dashboardRouter.get(
  "/trend",
  asyncHandler(async (req, res) => {
    const companyId = req.companyId;
    const period = req.query.period === "monthly" ? "monthly" : "weekly";
    const anchor = req.query.anchor || todayStr();
    const pad = (n) => String(n).padStart(2, "0");
    const toIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

    const anchorDate = new Date(anchor + "T00:00:00");
    let start, end, bucketBy;
    if (period === "monthly") {
      start = new Date(anchorDate.getFullYear(), anchorDate.getMonth(), 1);
      end = new Date(anchorDate.getFullYear(), anchorDate.getMonth() + 1, 0);
      bucketBy = "week";
    } else {
      const dow = anchorDate.getDay(); // 0=Sun..6=Sat
      const diffToMonday = (dow + 6) % 7;
      start = new Date(anchorDate);
      start.setDate(anchorDate.getDate() - diffToMonday);
      end = new Date(start);
      end.setDate(start.getDate() + 6);
      bucketBy = "day";
    }
    const startIso = toIso(start);
    const endIso = toIso(end);

    const companyBookingTables = await enabledBookingTables(companyId);
    const [paymentsResult, ...bookingResults] = await Promise.all([
      supabase.from("payment").select("date, final_amount, status").eq("company_id", companyId).eq("status", "Paid").gte("date", startIso).lte("date", endIso),
      ...companyBookingTables.map(({ type, table }) => {
        const dateColumn = type === "boarding" ? "check_in_date" : "booking_date";
        return supabase.from(table).select(`${dateColumn}, booking_status`).eq("company_id", companyId).gte(dateColumn, startIso).lte(dateColumn, endIso);
      }),
    ]);
    for (const r of [paymentsResult, ...bookingResults]) {
      if (r.error) return res.status(400).json({ error: r.error.message });
    }

    const allBookings = bookingResults.flatMap((result, index) => {
      const type = companyBookingTables[index].type;
      const dateColumn = type === "boarding" ? "check_in_date" : "booking_date";
      return result.data.map((booking) => ({ date: booking[dateColumn], status: booking.booking_status }));
    });

    const bucketRanges = [];
    if (bucketBy === "day") {
      for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
        const iso = toIso(d);
        bucketRanges.push({ label: iso, start: iso, end: iso });
      }
    } else {
      for (let cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 7)) {
        const weekStart = new Date(cursor);
        const weekEnd = new Date(cursor);
        weekEnd.setDate(weekEnd.getDate() + 6);
        const clampedEnd = weekEnd > end ? end : weekEnd;
        bucketRanges.push({ label: toIso(weekStart), start: toIso(weekStart), end: toIso(clampedEnd) });
      }
    }

    const buckets = bucketRanges.map(({ label, start: bStart, end: bEnd }) => {
      const bucketPayments = paymentsResult.data.filter((p) => p.date >= bStart && p.date <= bEnd);
      const bucketBookings = allBookings.filter(
        (b) => b.date >= bStart && b.date <= bEnd && b.status !== "Cancelled" && b.status !== "No Show"
      );
      return {
        label,
        start: bStart,
        end: bEnd,
        revenue: bucketPayments.reduce((sum, p) => sum + Number(p.final_amount || 0), 0),
        bookingCount: bucketBookings.length,
      };
    });

    res.json({ period, start: startIso, end: endIso, buckets });
  })
);

import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { makeCrudRouter } from "../lib/crudFactory.js";
import { requireManager } from "../middleware/authUser.js";

export const leaveRequestsRouter = makeCrudRouter({
  table: "leave",
  idColumn: "leave_id",
  searchableColumns: ["reason", "status"],
  defaultOrder: { column: "leave_id", ascending: false },
  updateMiddleware: [requireManager],
  deleteMiddleware: [requireManager],
  updatePayload: async () => {
    const err = new Error("Leave requests must be reviewed through the decision operation.");
    err.status = 405;
    throw err;
  },
  createPayload: async (req) => {
    let staffId = Number(req.body.staff_id);
    if (req.accountRole !== "manager") {
      const email = String(req.authUser?.email || "").trim().toLowerCase();
      const { data: staff, error } = await supabase
        .from("staff")
        .select("staff_id")
        .eq("company_id", req.companyId)
        .ilike("email", email)
        .maybeSingle();
      if (error || !staff) {
        const err = new Error("Your login email is not linked to a staff record. Ask a manager to update the staff email first.");
        err.status = 403;
        throw err;
      }
      staffId = staff.staff_id;
    }

    if (!Number.isInteger(staffId) || staffId <= 0 || !req.body.start_date || !req.body.end_date || !String(req.body.reason || "").trim()) {
      const err = new Error("Staff, start date, end date, and reason are required.");
      err.status = 400;
      throw err;
    }
    if (req.body.end_date < req.body.start_date) {
      const err = new Error("End date cannot be before start date.");
      err.status = 400;
      throw err;
    }

    const { data: companyStaff } = await supabase
      .from("staff")
      .select("staff_id")
      .eq("company_id", req.companyId)
      .eq("staff_id", staffId)
      .maybeSingle();
    if (!companyStaff) {
      const err = new Error("Staff record not found for this company.");
      err.status = 404;
      throw err;
    }

    const { date, time } = todayStamp();
    return {
      staff_id: staffId,
      start_date: req.body.start_date,
      end_date: req.body.end_date,
      reason: String(req.body.reason).trim(),
      status: "Pending",
      applied_date: date,
      applied_time: time,
      reviewed_by_staff_id: null,
      reviewed_date: null,
      reviewed_time: null,
    };
  },
  filterColumns: ["staff_id", "status"],
});

function todayStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: d.toTimeString().slice(0, 8),
  };
}

// POST /api/leave-requests/:id/decision   { status: "Approved" | "Rejected" }
leaveRequestsRouter.post(
  "/:id/decision",
  requireManager,
  asyncHandler(async (req, res) => {
    const { status } = req.body;
    if (!["Approved", "Rejected"].includes(status)) {
      return res.status(400).json({ error: "status must be 'Approved' or 'Rejected'." });
    }
    const { date, time } = todayStamp();
    const { data: reviewer } = await supabase
      .from("staff")
      .select("staff_id")
      .eq("company_id", req.companyId)
      .ilike("email", String(req.authUser?.email || "").trim())
      .maybeSingle();

    const { data, error } = await supabase.rpc("decide_leave_request", {
      p_company_id: req.companyId,
      p_leave_id: Number(req.params.id),
      p_status: status,
      p_reviewed_by: reviewer?.staff_id ?? null,
      p_reviewed_date: date,
      p_reviewed_time: time,
    });

    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

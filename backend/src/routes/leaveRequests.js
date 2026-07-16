import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { makeCrudRouter } from "../lib/crudFactory.js";

export const leaveRequestsRouter = makeCrudRouter({
  table: "leave",
  idColumn: "leave_id",
  searchableColumns: ["reason", "status"],
  defaultOrder: { column: "leave_id", ascending: false },
});

function todayStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: d.toTimeString().slice(0, 8),
  };
}

// POST /api/leave-requests/:id/decision   { status: "Approved" | "Rejected", reviewedByStaffId }
leaveRequestsRouter.post(
  "/:id/decision",
  asyncHandler(async (req, res) => {
    const { status, reviewedByStaffId } = req.body;
    if (!["Approved", "Rejected"].includes(status)) {
      return res.status(400).json({ error: "status must be 'Approved' or 'Rejected'." });
    }
    const { date, time } = todayStamp();

    const { data, error } = await supabase
      .from("leave")
      .update({
        status,
        reviewed_by_staff_id: reviewedByStaffId ?? null,
        reviewed_date: date,
        reviewed_time: time,
      })
      .eq("company_id", req.companyId)
      .eq("leave_id", req.params.id)
      .select()
      .single();

    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

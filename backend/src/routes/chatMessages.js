import { makeCrudRouter } from "../lib/crudFactory.js";
import { supabase } from "../supabaseClient.js";
import { requireManager } from "../middleware/authUser.js";

function localStamp() {
  const date = new Date();
  const pad = value => String(value).padStart(2, "0");
  return {
    date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    time: `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  };
}

export const chatMessagesRouter = makeCrudRouter({
  table: "messages",
  idColumn: "message_id",
  searchableColumns: ["message_text", "intent_label"],
  defaultOrder: { column: "message_id", ascending: false },
  deleteMiddleware: [requireManager],
  createPayload: async req => {
    const senderId = Number(req.body.sender_id);
    const messageText = String(req.body.message_text || "").trim();
    if (!Number.isInteger(senderId) || senderId <= 0 || !messageText) {
      const error = new Error("Customer and message are required.");
      error.status = 400;
      throw error;
    }
    const { data: customer } = await supabase
      .from("customer")
      .select("customer_id")
      .eq("company_id", req.companyId)
      .eq("customer_id", senderId)
      .maybeSingle();
    if (!customer) {
      const error = new Error("Customer not found for this company.");
      error.status = 404;
      throw error;
    }
    const stamp = localStamp();
    return {
      sender_type: "customer",
      sender_id: senderId,
      message_text: messageText,
      intent_label: String(req.body.intent_label || "General").trim() || "General",
      receive_date: req.body.receive_date || stamp.date,
      receive_time: req.body.receive_time || stamp.time,
      reply_date: null,
      reply_time: null,
      reply_text: null,
      replied_by_staff_id: null,
    };
  },
  updatePayload: async req => {
    const stamp = localStamp();
    const replyText = String(req.body.reply_text || "").trim();
    if (!replyText) {
      const error = new Error("Reply text cannot be empty.");
      error.status = 400;
      throw error;
    }
    const payload = {
      reply_date: req.body.reply_date || stamp.date,
      reply_time: req.body.reply_time || stamp.time,
    };
    payload.reply_text = replyText;

    const { data: reviewer } = await supabase
      .from("staff")
      .select("staff_id")
      .eq("company_id", req.companyId)
      .ilike("email", String(req.authUser?.email || "").trim())
      .maybeSingle();
    payload.replied_by_staff_id = reviewer?.staff_id || null;
    return payload;
  },
  filterColumns: ["sender_id", "sender_type", "intent_label"],
});

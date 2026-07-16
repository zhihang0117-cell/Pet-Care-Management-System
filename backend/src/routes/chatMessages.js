import { makeCrudRouter } from "../lib/crudFactory.js";

export const chatMessagesRouter = makeCrudRouter({
  table: "messages",
  idColumn: "message_id",
  searchableColumns: ["message_text", "intent_label"],
  defaultOrder: { column: "message_id", ascending: false },
});

import { makeCrudRouter } from "../lib/crudFactory.js";

export const staffRouter = makeCrudRouter({
  table: "staff",
  idColumn: "staff_id",
  searchableColumns: ["staff_name", "role", "email"],
  defaultOrder: { column: "staff_id", ascending: true },
});

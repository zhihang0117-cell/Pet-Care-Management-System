import { makeCrudRouter } from "../lib/crudFactory.js";

export const customersRouter = makeCrudRouter({
  table: "customer",
  idColumn: "customer_id",
  searchableColumns: ["full_name", "phone_number", "address"],
  defaultOrder: { column: "customer_id", ascending: true },
});

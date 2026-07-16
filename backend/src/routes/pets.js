import { makeCrudRouter } from "../lib/crudFactory.js";

export const petsRouter = makeCrudRouter({
  table: "pet",
  idColumn: "pet_id",
  searchableColumns: ["pet_name", "breed", "pet_type"],
  defaultOrder: { column: "pet_id", ascending: true },
});

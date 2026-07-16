import { makeCrudRouter } from "../lib/crudFactory.js";

export const couponsRouter = makeCrudRouter({
  table: "coupon",
  idColumn: "coupon_id",
  searchableColumns: ["reward_name", "reward_type"],
  defaultOrder: { column: "points_required", ascending: true },
});

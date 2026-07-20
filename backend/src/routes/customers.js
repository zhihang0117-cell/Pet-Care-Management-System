import { makeCrudRouter } from "../lib/crudFactory.js";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

export const customersRouter = makeCrudRouter({
  table: "customer",
  idColumn: "customer_id",
  searchableColumns: ["full_name", "phone_number", "address"],
  defaultOrder: { column: "customer_id", ascending: true },
});

// Deletes the customer and linked pets in one database transaction. Any
// booking/loyalty foreign-key dependency aborts the whole operation.
customersRouter.delete(
  "/:id/with-pets",
  asyncHandler(async (req, res) => {
    const { error } = await supabase.rpc("delete_customer_with_pets", {
      p_company_id: req.companyId,
      p_customer_id: Number(req.params.id),
    });
    if (error) return res.status(400).json({ error: error.message });
    res.status(204).end();
  })
);

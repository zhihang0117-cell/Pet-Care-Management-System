import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";

const RESERVED_QUERY_KEYS = new Set(["search", "limit", "offset", "order", "ascending"]);

/**
 * Builds a full CRUD router for a table that doesn't need special business
 * logic beyond "scope to company_id".
 *
 *   table              - table name in Supabase
 *   idColumn            - primary key column name
 *   searchableColumns    - text columns `?search=` will ilike-match against
 *   defaultOrder         - { column, ascending }
 *
 * Query-string filters: any query param that isn't one of the reserved
 * keys above is applied as an equality filter, e.g.
 *   GET /api/pets?customer_id=3
 *   GET /api/bookings/grooming?booking_status=pending
 */
export function makeCrudRouter({ table, idColumn, searchableColumns = [], defaultOrder }) {
  const router = Router();

  router.get(
    "/",
    asyncHandler(async (req, res) => {
      let query = supabase.from(table).select("*").eq("company_id", req.companyId);

      for (const [key, value] of Object.entries(req.query)) {
        if (RESERVED_QUERY_KEYS.has(key)) continue;
        query = query.eq(key, value);
      }

      if (req.query.search && searchableColumns.length) {
        const orFilter = searchableColumns
          .map((col) => `${col}.ilike.%${req.query.search}%`)
          .join(",");
        query = query.or(orFilter);
      }

      const orderColumn = req.query.order || defaultOrder?.column;
      if (orderColumn) {
        const ascending =
          req.query.ascending !== undefined
            ? req.query.ascending === "true"
            : defaultOrder?.ascending ?? true;
        query = query.order(orderColumn, { ascending });
      }

      if (req.query.limit) query = query.limit(Number(req.query.limit));
      if (req.query.offset) query = query.range(Number(req.query.offset), Number(req.query.offset) + Number(req.query.limit || 50) - 1);

      const { data, error } = await query;
      if (error) return res.status(400).json({ error: error.message });
      res.json(data);
    })
  );

  router.get(
    "/:id",
    asyncHandler(async (req, res) => {
      const { data, error } = await supabase
        .from(table)
        .select("*")
        .eq("company_id", req.companyId)
        .eq(idColumn, req.params.id)
        .single();
      if (error) return res.status(404).json({ error: `${table} ${req.params.id} not found` });
      res.json(data);
    })
  );

  router.post(
    "/",
    asyncHandler(async (req, res) => {
      const payload = { ...req.body, company_id: req.companyId };
      delete payload[idColumn]; // let the DB assign the primary key
      const { data, error } = await supabase.from(table).insert(payload).select().single();
      if (error) return res.status(400).json({ error: error.message });
      res.status(201).json(data);
    })
  );

  router.patch(
    "/:id",
    asyncHandler(async (req, res) => {
      const payload = { ...req.body };
      delete payload[idColumn];
      delete payload.company_id; // never let a client move a row to another tenant
      const { data, error } = await supabase
        .from(table)
        .update(payload)
        .eq("company_id", req.companyId)
        .eq(idColumn, req.params.id)
        .select()
        .single();
      if (error) return res.status(400).json({ error: error.message });
      res.json(data);
    })
  );

  router.delete(
    "/:id",
    asyncHandler(async (req, res) => {
      const { error } = await supabase
        .from(table)
        .delete()
        .eq("company_id", req.companyId)
        .eq(idColumn, req.params.id);
      if (error) return res.status(400).json({ error: error.message });
      res.status(204).end();
    })
  );

  return router;
}

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
export function makeCrudRouter({
  table,
  idColumn,
  searchableColumns = [],
  defaultOrder,
  createMiddleware = [],
  updateMiddleware = [],
  deleteMiddleware = [],
  createPayload,
  updatePayload,
  filterColumns = [],
}) {
  const router = Router();

  router.get(
    "/",
    asyncHandler(async (req, res) => {
      let query = supabase.from(table).select("*").eq("company_id", req.companyId);

      for (const [key, value] of Object.entries(req.query)) {
        if (RESERVED_QUERY_KEYS.has(key)) continue;
        if (!filterColumns.includes(key)) {
          return res.status(400).json({ error: `Unsupported filter: ${key}` });
        }
        query = query.eq(key, value);
      }

      if (req.query.search && searchableColumns.length) {
        const orFilter = searchableColumns
          .map((col) => `${col}.ilike.%${req.query.search}%`)
          .join(",");
        query = query.or(orFilter);
      }

      const allowedOrderColumns = new Set([idColumn, defaultOrder?.column, ...searchableColumns, ...filterColumns].filter(Boolean));
      const orderColumn = req.query.order || defaultOrder?.column;
      if (orderColumn && !allowedOrderColumns.has(orderColumn)) {
        return res.status(400).json({ error: `Unsupported order column: ${orderColumn}` });
      }
      if (orderColumn) {
        const ascending =
          req.query.ascending !== undefined
            ? req.query.ascending === "true"
            : defaultOrder?.ascending ?? true;
        query = query.order(orderColumn, { ascending });
      }

      const requestedLimit = req.query.limit === undefined ? null : Number(req.query.limit);
      const requestedOffset = req.query.offset === undefined ? null : Number(req.query.offset);
      if (requestedLimit !== null && (!Number.isInteger(requestedLimit) || requestedLimit < 1 || requestedLimit > 200)) {
        return res.status(400).json({ error: "limit must be an integer from 1 to 200." });
      }
      if (requestedOffset !== null && (!Number.isInteger(requestedOffset) || requestedOffset < 0)) {
        return res.status(400).json({ error: "offset must be a non-negative integer." });
      }
      if (requestedLimit !== null) query = query.limit(requestedLimit);
      if (requestedOffset !== null) query = query.range(requestedOffset, requestedOffset + (requestedLimit || 50) - 1);

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
    ...createMiddleware,
    asyncHandler(async (req, res) => {
      const requestedPayload = createPayload ? await createPayload(req) : req.body;
      const payload = { ...requestedPayload, company_id: req.companyId };
      delete payload[idColumn]; // let the DB assign the primary key
      const { data, error } = await supabase.from(table).insert(payload).select().single();
      if (error) return res.status(400).json({ error: error.message });
      res.status(201).json(data);
    })
  );

  router.patch(
    "/:id",
    ...updateMiddleware,
    asyncHandler(async (req, res) => {
      const requestedPayload = updatePayload ? await updatePayload(req) : req.body;
      const payload = { ...requestedPayload };
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
    ...deleteMiddleware,
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

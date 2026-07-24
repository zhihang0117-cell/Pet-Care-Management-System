export function parsePagination(query, { defaultLimit = null, maxLimit = 200 } = {}) {
  const limit = query.limit === undefined ? defaultLimit : Number(query.limit);
  const offset = query.offset === undefined ? 0 : Number(query.offset);
  if (limit !== null && (!Number.isInteger(limit) || limit < 1 || limit > maxLimit)) {
    const error = new Error(`limit must be an integer from 1 to ${maxLimit}.`);
    error.status = 400;
    throw error;
  }
  if (!Number.isInteger(offset) || offset < 0) {
    const error = new Error("offset must be a non-negative integer.");
    error.status = 400;
    throw error;
  }
  return { limit, offset };
}

export function assertAllowedQueryKeys(query, allowed) {
  const allowedSet = new Set(allowed);
  const unsupported = Object.keys(query).find(key => !allowedSet.has(key));
  if (unsupported) {
    const error = new Error(`Unsupported query parameter: ${unsupported}`);
    error.status = 400;
    throw error;
  }
}

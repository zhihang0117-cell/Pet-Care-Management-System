import "dotenv/config";

/** Small helper so route handlers don't all repeat the same try/catch. */
export function asyncHandler(fn) {
  return (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
}

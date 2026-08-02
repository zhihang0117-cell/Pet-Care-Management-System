const buckets = new Map();

function positiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

/** Small in-process limiter for the current single Node instance deployment. */
export function apiRateLimit(req, res, next) {
  const limit = positiveInt(process.env.API_RATE_LIMIT_PER_MINUTE, 120);
  const windowMs = 60_000;
  const now = Date.now();
  const key = String(req.ip || req.socket?.remoteAddress || "unknown");
  const current = buckets.get(key);
  const bucket = !current || current.resetAt <= now
    ? { count: 0, resetAt: now + windowMs }
    : current;
  bucket.count += 1;
  buckets.set(key, bucket);
  res.setHeader("RateLimit-Limit", String(limit));
  res.setHeader("RateLimit-Remaining", String(Math.max(0, limit - bucket.count)));
  res.setHeader("RateLimit-Reset", String(Math.ceil(bucket.resetAt / 1000)));

  if (buckets.size > 10_000) {
    for (const [bucketKey, value] of buckets) {
      if (value.resetAt <= now) buckets.delete(bucketKey);
      if (buckets.size <= 8_000) break;
    }
  }
  if (bucket.count > limit) {
    res.setHeader("Retry-After", String(Math.max(1, Math.ceil((bucket.resetAt - now) / 1000))));
    return res.status(429).json({ error: "Too many requests; please retry shortly." });
  }
  next();
}

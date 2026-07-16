import { createClient } from "@supabase/supabase-js";
import "dotenv/config";

const url = process.env.SUPABASE_URL;
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!url || !serviceRoleKey) {
  throw new Error(
    "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY. Copy .env.example to .env and fill them in."
  );
}

// IMPORTANT: this client uses the service_role key, which bypasses Row Level
// Security. That is intentional here — this file is only ever imported by
// backend code that runs on your server, never sent to the browser. The
// browser/LLM only ever talk to *this* Express API, never to Supabase directly.
export const supabase = createClient(url, serviceRoleKey, {
  auth: { persistSession: false },
});

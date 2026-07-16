// Shared Supabase client for the frontend. Load the Supabase JS CDN script
// BEFORE this file on any page that needs it:
//   <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.js"></script>
//   <script src="supabase-config.js"></script>
//
// This uses the PUBLISHABLE/ANON key only — safe to ship to the browser.
// Row Level Security (see backend/sql/rls_policies.sql) is what actually
// protects data if this key is ever used to talk to Supabase directly.
// The service_role key (secret, full access, bypasses RLS) never belongs
// here — it lives only in backend/.env, read by the Express server.
const SUPABASE_URL = "https://xlrrptgtyrmdpzqfvqjo.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_EZTjJhJ_oaX9TgNeTojpRQ_L8bTLDCN";

const supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

/** Returns the current session's access token, or null if not logged in. */
async function getSupabaseAccessToken() {
    const { data } = await supabaseClient.auth.getSession();
    return data.session?.access_token || null;
}

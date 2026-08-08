# PAWFECT (merged)

This project supersedes the two earlier prototypes (`PAWFECT_LLM` and
`PAWFECT_Refined_LangChain_Design`). The LangChain tool-calling harness below
is the architecture that was kept; the older multi-chain
extractor/router/resolver/workflow pipeline (single-scenario, Grooming-only)
was retired in favor of it and is not part of this codebase. The only pieces
carried over from the older prototype are non-behavioral, additive ones:
`app/tools/text_formatting.py` (deterministic name/pet-list formatting,
wired into `orchestrator.py`'s identity resolution and referenced by rule 12
of the system prompt).

This is the simplified version of the previous PAWFECT orchestrator structure.

It follows the lecturer's idea of:
**Prompt -> Context -> Harness**

without introducing unnecessary Loop/Graph/multi-agent complexity.

## Core folders

- `prompts/` — global LLM role/rules/examples
- `context/` — conversation state and memory
- `scenarios/` — business-flow definitions
- `tools/` — approved LangChain tools
- `rag/` — tenant-aware policy retrieval
- `db/` — Supabase runtime repository boundary
- `validation/` — vaccination-policy check used by create_booking/reschedule_booking
- `documents/` — booking-confirmation/invoice PDF generation, WhatsApp
  dispatch (Meta Cloud API in production, console provider for local UAT),
  and status/redemption/refund customer notices
- `integrations/` — development integrations such as Supabase MCP
- `orchestrator.py` — LangChain + GPT-4o mini harness
- `main.py` (project root) — FastAPI HTTP entrypoint exposing the harness to
  frontend/eval_console.html and, eventually, a WhatsApp webhook

## Production idea

WhatsApp
-> LangChain harness
-> GPT-4o mini
-> approved tools
-> Supabase / RAG
-> validation
-> response

## Supabase MCP

Supabase MCP is kept as a development/debugging integration, not the customer's
direct production database interface.

See:
`app/integrations/supabase_mcp.md`
# Production AI/WhatsApp configuration

The Python `/chat` endpoint fails closed in production unless `CHAT_API_KEY`
is configured and supplied as `X-Chat-Key`. Server-to-server document calls
use a separate shared `INTERNAL_API_KEY`/`AI_BACKEND_INTERNAL_KEY`. Both
backends have in-process rate limits sized for the current single-instance
deployment.

Real WhatsApp delivery uses Meta Cloud API when these variables are set:

```text
WHATSAPP_PROVIDER=meta_cloud_api
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_GRAPH_API_VERSION=v23.0
```

`WHATSAPP_PROVIDER=console` remains local-test-only. With no provider, the API
returns `delivery.status=not_configured` rather than pretending a message was
sent.

## Deployment (Google Cloud Run)

This service (the `Dockerfile` at the repository root) is built for Cloud
Run: it reads `PORT` from the environment and listens on `0.0.0.0`, matching
Cloud Run's container contract exactly, so no Dockerfile changes are needed.
The items below are NOT optional defaults — skipping any of them causes a
real, specific failure mode particular to this service, not a generic "it
might run slower" caveat.

```bash
gcloud run deploy pawfectai-ai \
  --source . \
  --region asia-southeast1 \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --min-instances 1 \
  --max-instances 1 \
  --set-env-vars "RELATIONAL_COMPANY_ID=1,PAWFECT_DEBUG_MODE=false,TOOL_EXECUTOR_MAX_WORKERS=4,TOOL_CALL_TIMEOUT_SECONDS=20,EMBEDDING_PROVIDER=sentence_transformers,EMBEDDING_MODEL=BAAI/bge-large-en-v1.5,SUPABASE_STORAGE_BUCKET=business-documents,WHATSAPP_PROVIDER=meta_cloud_api" \
  --set-secrets "OPENAI_API_KEY=openai-api-key:latest,SUPABASE_URL=supabase-url:latest,SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest,INTERNAL_API_KEY=internal-api-key:latest,CHAT_API_KEY=chat-api-key:latest,WHATSAPP_ACCESS_TOKEN=whatsapp-access-token:latest,WHATSAPP_PHONE_NUMBER_ID=whatsapp-phone-number-id:latest"
```

(Create those Secret Manager secrets first — `gcloud secrets create supabase-url --data-file=-`, etc. — or swap `--set-secrets` for a second `--set-env-vars` block if you'd rather manage them as plain env vars; the `SUPABASE_SERVICE_ROLE_KEY` and the API keys are the ones actually worth keeping in Secret Manager.)

At startup, the service performs a read-only PostgREST OpenAPI preflight and
refuses to serve traffic when a required database RPC is missing. Apply the
manual migrations in `backend/sql/README.md` before deploying; in particular,
membership registration requires `loyalty_member_registration_migration.sql`.

- **`OPENAI_API_KEY` is required for every single `/chat` call, not just RAG**
  — `app/orchestrator.py`'s `ChatOpenAI(...)` (the gpt-4o-mini call itself)
  reads this from the environment; it is unrelated to `EMBEDDING_PROVIDER`
  (which only controls embeddings, and defaults to a local model that needs
  no OpenAI key at all). Missing this means every conversation fails at the
  very first model call, not a degraded-but-working mode.
- **`--allow-unauthenticated` is required, not a security downgrade** — this
  service already gates every real ingress path itself (`X-Chat-Key` for
  `/chat`, `X-Internal-Key` for the `/documents/*`/`/api/documents/*`
  server-to-server routes). Cloud Run's own IAM auth would additionally
  require the Node backend to mint Google-signed identity tokens on every
  call for no real security gain here, and the eval console / a future
  WhatsApp webhook have no way to do that at all.
- **`--memory 4Gi` (or at least 2Gi) is required, not a nice-to-have** —
  `sentence-transformers` + `torch` + the loaded BGE-Large model need
  substantially more RAM than Cloud Run's default allocation (512Mi). Under
  that default the container OOM-kills itself the first time
  `retrieve_policy`/`get_booking_service_options` actually loads the model —
  it looks like a random mid-request crash, not an obvious memory error,
  unless you already know to look for it.
- **`--min-instances 1 --max-instances 1` is required for correctness, not
  just cost** — `app/context/memory.py` (conversation state) is still an
  in-process store, so Cloud Run's default autoscaling could route consecutive
  messages to different instances and silently lose conversation context.
  Slot holds use the shared Supabase `booking_slot_hold` table after
  `backend/sql/booking_slot_holds_migration.sql` is applied, with the local
  registry retained only as a compatibility fallback; without that migration,
  multiple workers can still offer the same provisional slot. Pinning to one
  instance remains the correct interim deployment until conversation state is
  also shared. `min-instances=1` also avoids the
  BGE-Large model being re-downloaded from Hugging Face on every cold start
  (the container filesystem is not persisted across restarts), which
  `main.py`'s startup warmup hook (`_warm_up_embedding_model`) would
  otherwise pay for on every single one.
- `--timeout 300` gives a slow multi-tool-call turn (see
  `MAX_TOOL_ITERATIONS` in `app/orchestrator.py`) more room than Cloud Run's
  older default; raise it further only if you see real timeouts in logs.

After deploying, set `CLOUD_RUN_AI_BACKEND_URL` (this service's URL) and
`CLOUD_RUN_AI_BACKEND_INTERNAL_KEY` (matching `INTERNAL_API_KEY` above) on
the Render-deployed Node backend — see `backend/README.md` §3.

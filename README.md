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

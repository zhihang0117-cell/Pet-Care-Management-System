import { Router } from "express";
import { asyncHandler, requireLlmKey } from "../middleware/auth.js";
import { TOOL_SCHEMA, executeTool } from "../llm/tools.js";

export const llmRouter = Router();
llmRouter.use(requireLlmKey);

// GET /api/llm/tools-schema
// Feed this straight into an Anthropic `tools` array (or reshape input_schema
// -> parameters for OpenAI-style function calling).
llmRouter.get("/tools-schema", (req, res) => {
  res.json(TOOL_SCHEMA);
});

// POST /api/llm/execute   { tool: "verify_payment", input: { payment_id: 42, staff_id: 1 } }
// Pass which business this is for via the x-company-id header (same as the
// frontend does) — an LLM agent should be told the company_id up front by
// whatever system prompt/session set it up.
llmRouter.post(
  "/execute",
  asyncHandler(async (req, res) => {
    const { tool, input } = req.body;
    if (!tool) return res.status(400).json({ error: "Body must include a 'tool' name." });

    const result = await executeTool(req.companyId, tool, input || {});
    res.json({ tool, result });
  })
);

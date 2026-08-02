# Supabase MCP in PAWFECT

Use Supabase MCP primarily for DEVELOPMENT / DEBUGGING.

Recommended use:
- inspect schema
- inspect tables
- check logs
- assist with migrations
- development queries
- generate types / understand database structure

Do NOT expose the hosted Supabase MCP directly to WhatsApp customers.

Production runtime should use narrow Python/LangChain business tools such as:
- get_customer
- check_availability
- create_booking
- cancel_booking
- reschedule_booking

Keep destructive actions unavailable.

Recommended separation:

Developer / VS Code
    -> Supabase MCP
    -> Supabase

WhatsApp customer
    -> LangChain harness
    -> approved business tools
    -> Supabase

# factuurcontrole

Take-home for The New Wave IT: an API service that checks draft invoices before they are sent. The brief and plan are in PLAN-factuurcontrole-done.md (a local file, not in the public repo).

Architecture: read ARCHITECTURE.md, docs/adr/ and GLOSSARY.md before changing the shape of the code. Accepted ADRs are decisions, not suggestions; a change that needs one reversed stops and says so. Use the words from GLOSSARY.md in code, comments and conversation.

Code and API names in English; README, the architecture drawing and the messages a billing employee reads in Dutch. Synthetic data only. Secrets come from .env by name and are never printed, logged or committed. Claude is called through the claude CLI with CLAUDE_CODE_OAUTH_TOKEN, never with an API key.

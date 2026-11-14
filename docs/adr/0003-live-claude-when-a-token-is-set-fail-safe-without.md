# 0003 - Live Claude when a token is set; fail-safe without; recorded answers for the eval only

Status: accepted (user decision, 2026-11-14)

Context: reviewers may or may not have a Claude token; the case says no costs are needed.

Decision: `ModelClient` with three adapters. The service uses the Claude CLI with `CLAUDE_CODE_OAUTH_TOKEN` when set, else `Unavailable`, which makes every AI part "controle nodig: niet uitgevoerd". The eval script uses `Recorded` (cassettes keyed by the hash of the masked prompt) for a free, repeatable scoreboard, and live once to compare. Every rule and finding carries the mode it came from.

Why: the service runs for every reviewer and never pretends to be live, and the scoreboard is reproducible without a key. Reversing to live-only makes the first run fail for a reviewer without a token.

Rejected: live only (first run fails without a token); recorded answers inside the service (a reviewer would get AI findings that were not produced on their machine); an API-key SDK client (the owner's standing rule: subscription CLI only).

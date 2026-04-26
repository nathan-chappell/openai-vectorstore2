---
name: openai-log-debugger
description: Use when debugging this app's OpenAI Responses or Conversations from backend logs, platform log URLs, resp_ IDs, conv_ IDs, or ChatKit/OpenAI request failures; fetches enough OpenAI API artifact data to troubleshoot and guides Codex to inspect logs, diagnose, fix, and verify local issues.
---

# OpenAI Log Debugger

Use this skill in `openai-vectorstore2` when the user provides an OpenAI platform log URL, a `resp_...`
or `conv_...` ID, or asks to debug a ChatKit/OpenAI backend failure from logs.

## Workflow

1. Search the backend log first:

   ```bash
   rg -n "resp_|conv_|openai_log_url|conversation_log_url|openai_response|chat_turn" .local/logs
   ```

2. Fetch the relevant OpenAI artifact. Prefer explicit IDs or URLs from the user; otherwise let the script scan
   `.local/logs/openai-vectorstore2.log`.

   ```bash
   python skills/openai-log-debugger/scripts/fetch_openai_artifact.py https://platform.openai.com/logs/resp_123
   python skills/openai-log-debugger/scripts/fetch_openai_artifact.py --limit 5
   ```

   The script reads `OPENAI_API_KEY` from the environment or `.env`, writes JSON under `.local/openai-debug/`,
   and prints the local artifact path plus the clickable platform log URL.

3. Inspect the downloaded JSON and nearby backend logs. Focus on response `status`, `error`,
   `incomplete_details`, `model`, `usage`, tool calls, conversation items, and the local operation/thread IDs
   logged near the OpenAI ID.

4. Correlate the artifact with app code:

   - Direct Responses calls usually go through `backend/app/integrations/openai_gateway.py`.
   - ChatKit agent turns usually go through `backend/app/chatkit/server.py`.
   - Import/indexing failures usually involve `backend/app/services/research.py` or
     `backend/app/services/sources.py`.

5. If the artifact points to a local bug, patch the code, update `plan.md`, and run focused tests. Avoid pasting
   raw prompts, secrets, or large response bodies back to the user; summarize only the troubleshooting-relevant
   fields.

Official API surfaces used by the script:

- `GET /v1/responses/{response_id}`
- `GET /v1/conversations/{conversation_id}`
- `GET /v1/conversations/{conversation_id}/items`

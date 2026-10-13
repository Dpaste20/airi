# airi-tui

OpenTUI (React) terminal client for the Airi FastAPI server (`server.py`).

```bash
# terminal 1
python server.py          

# terminal 2
cd airi-tui
bun install
bun start                     # AIRI_URL=http://host:8000 to override
```

Keys: `enter` send · `esc` interrupt · `ctrl+n` new session · `ctrl+l` clear view · `ctrl+c` quit
Commands: `/new`, `/clear`, `/exit`

Talks to `GET /health` (status dot) and `POST /chat/stream` (SSE `{delta}` / `[DONE]` / `{error}`).
Requires Bun (OpenTUI runs on Bun).

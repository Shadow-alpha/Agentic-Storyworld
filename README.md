# Agentic Storyworld

Agentic Storyworld is a local-first interactive story runtime. It uses a FastAPI backend, a Vue frontend, and LLM-driven Director/Character agents to run multi-character narrative games from files under `games/`.

## Requirements

- Python 3.10+
- Node.js 20+
- npm
- An OpenAI-compatible LLM API key

The project has been tested locally with a Conda environment named `agentMem`, but Conda is not required.

## Setup

1. Install backend dependencies.

```powershell
python -m pip install -r requirements.txt
```

If you use Conda:

```powershell
conda create -n agentMem python=3.10
conda activate agentMem
python -m pip install -r requirements.txt
```

2. Install frontend dependencies.

```powershell
cd frontend
npm install
cd ..
```

3. Create local config.

```powershell
Copy-Item config.example.json config.json
```

Then edit `config.json`:

```json
{
  "game": {
    "default_game_id": "doupo_wutan",
    "games_dir": "games",
    "users_dir": "users"
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "access": {
    "enabled": true,
    "invite_codes": ["root"],
    "token_secret": "replace-with-a-random-secret"
  }
}
```

Do not commit `config.json`. It contains local secrets such as API keys and invite settings.

## Game Data

Game configuration files live under:

```text
games/<game_id>/base/
```

User runtime data lives under:

```text
users/<user_id>/<game_id>/
```

For invite-code testing, `user_id` is generated from the invite code. For example, invite code `root` uses:

```text
users/root/<game_id>/
```

Both `games/` and `users/` are intentionally ignored by Git in this repository.

## Run Locally

Build the frontend first:

```powershell
cd frontend
npm run build
cd ..
```

Then start the backend:

```powershell
python -m src.app_server
```

With Conda:

```powershell
conda run -n agentMem python -m src.app_server
```

Open:

```text
http://127.0.0.1:8000/
```

`npm run build` does not print a browser URL. It only generates `frontend/dist`. The FastAPI server serves that built frontend.

## Invite-Code Testing

If `access.enabled` is `true`, the page first asks for an invite code.

Default local example:

```text
root
```

The frontend stores a signed access token in `localStorage`. To force the invite-code screen again, use the toolbar invite button or run this in the browser console:

```js
localStorage.removeItem("access_token")
location.reload()
```

## Frontend Development Mode

For frontend-only iteration:

```powershell
cd frontend
npm run dev
```

This starts Vite's dev server. Production-like local testing should use:

```powershell
npm run build
python -m src.app_server
```

## Useful Checks

Backend syntax check:

```powershell
python -m py_compile src\app_server.py src\main.py src\state_manager.py
```

Frontend build check:

```powershell
cd frontend
npm run build
```

API smoke checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/access/status
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## Local Troubleshooting

- If the page is blank after rebuilding, press `Ctrl + F5`.
- If the page still uses an old invite token, clear `localStorage.access_token`.
- If module scripts fail with MIME errors, restart `python -m src.app_server`; the backend explicitly registers `.js`, `.mjs`, and `.css` MIME types.
- If another process is using port `8000`, stop it or change `server.port` in `config.json`.

## Internal Test Deployment Notes

For a simple private test deployment:

1. Build frontend with `npm run build`.
2. Run `python -m src.app_server` behind a process manager such as `systemd` or `supervisor`.
3. Keep `config.json`, `games/`, and `users/` on the server but out of Git.
4. Back up `users/` regularly; it contains player runtime state, logs, saves, and memories.
5. Use a strong `access.token_secret` before sharing invite codes.

# Frontend

React + Vite + TypeScript frontend for the current FastAPI backend.

## Run

### Local npm

```bash
cd frontend
npm install
npm run dev
```

By default Vite proxies `/api` to `http://localhost:8000`.

If your backend runs elsewhere, create `frontend/.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_DEV_PROXY_TARGET=http://localhost:8000
```

### Docker Compose

From the repo root:

```bash
docker compose --env-file .env.development up -d --build app frontend
```

Then open `http://localhost:5173`.

## Current Flow

1. Login or register
2. Backend creates an auth session
3. Create or select a chat thread
4. Send messages through `/api/v1/chatbot/chat/stream`
5. Clarification events render as buttons

The web UI persists thread/timeline data through:

- `/api/v1/chatbot/threads`
- `/api/v1/chatbot/threads/{thread_id}/items`

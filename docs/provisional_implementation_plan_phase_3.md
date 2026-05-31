# Phase 3: Live API & Webhook Layer

We are transitioning Tesson from a set of Python libraries into a persistent, live application. To use the Asynchronous Double-Buffering Engine correctly, the Python process must stay alive indefinitely to manage the background recompilation thread and serve incoming network requests instantly.

## User Review Required

> [!IMPORTANT]
> I propose using **FastAPI** as the web framework. It has native support for asynchronous workflows, Pydantic validation (which we already heavily use), and auto-generates Swagger documentation. Let me know if you prefer a different framework (e.g., Flask or Django).

## Open Questions

> [!WARNING]
> Do you want the GitHub Webhook ingestion (`POST /webhooks/github`) to block and wait for the LLM to finish extracting the PR, or should it immediately return a `202 Accepted` and run the LLM extraction in a background task? (Recommended: Background task, as GitHub webhooks expect quick responses).

## Proposed Changes

---

### API Server Configuration & Lifespan

We need a central FastAPI application that manages the `TessonEngine` singleton.

#### [NEW] [server.py](file:///c:/Code_One/ANTIGRAVITY/tesson/project_tesson/src/api/server.py)
- Create a FastAPI application instance.
- Implement `@asynccontextmanager` for server lifespan:
  - **Startup**: Initialize `TessonEngine` and call `start_background_worker()`.
  - **Shutdown**: Call `stop_background_worker()` to gracefully terminate the double-buffer compilation.
- Inject the engine dependency into routes using FastAPI's `Depends`.

---

### RCA Query Endpoint

The endpoint that Datadog/PagerDuty will call when an incident occurs.

#### [NEW] [rca_routes.py](file:///c:/Code_One/ANTIGRAVITY/tesson/project_tesson/src/api/routes/rca_routes.py)
- Define `POST /api/v1/rca`.
- Accept the `AlertEvent` Pydantic model (created in Phase 2).
- Call the global `TessonEngine.query_rca()`.
- Return an `RCAResponse` model containing the ranked root causes.

---

### Ingestion Webhook Endpoint

The endpoint that GitHub calls when a developer merges a Pull Request.

#### [NEW] [ingestion_routes.py](file:///c:/Code_One/ANTIGRAVITY/tesson/project_tesson/src/api/routes/ingestion_routes.py)
- Define `POST /api/v1/webhooks/github`.
- Accept GitHub webhook JSON payloads.
- Trigger the Phase 1 `Hydrator` workflow to fetch the Git diff, run the LLM extraction, resolve via TERL, and append to the immutable ledger.

---

## Verification Plan

### Automated Tests
- `pytest tests/test_api/test_server.py`: Use FastAPI's `TestClient` to verify the application lifespan starts and stops the background thread correctly.
- `pytest tests/test_api/test_rca_routes.py`: Send a mock Datadog alert to the RCA endpoint and verify it returns a 200 OK with correct JSON.

### Manual Verification
- We will start the live server using `uvicorn src.api.server:app --reload`.
- We will use `curl` to fire a live Datadog alert to the RCA endpoint and watch it return the root causes in under 10 milliseconds.

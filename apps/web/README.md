# Web application

Vue 3 + TypeScript + Vite frontend for Agent Task Platform.

## Commands

```bash
npm install
npm run dev
npm test
npm run build
```

The development server proxies `/api/*` to `http://127.0.0.1:8080/api/*` without rewriting the path. All JSON calls use the canonical `/api/v1` API. The proxy keeps the browser-facing `Host` for WebSocket origin checks; production reverse proxies must likewise preserve the original host instead of disabling origin validation globally.

## Source layout

- `src/pages`: route-level adapters. Pages should remain thin.
- `src/features`: feature-owned UI, API maps, domain types, composables, and utilities.
- `src/shared`: cross-feature infrastructure such as the authenticated HTTP transport.
- `src/components`: reusable application-level UI components.
- `src/stores`: application state and API composition.

The shared HTTP client unwraps successful `{ data, meta }` responses and raises `ApiError` for `{ error: { code, message, details, request_id } }`. Artifact downloads intentionally bypass the JSON envelope.

### Presentation feature

`src/features/presentations` is split by responsibility:

- `api.ts` and `types.ts`: public feature contract.
- `components/PresentationWorkspace.vue`: owns a map of concurrent generation runs, task selection, and per-run cancellation/download state.
- `components/PresentationComposer.vue`, `TaskEventTimeline.vue`, and `PagePreviewGrid.vue`: focused input, progress, and artifact UI.
- `websocket.ts`: maintains one authenticated `/api/v1/ppt/ws` connection, multiplexes all run subscriptions, and resumes each run from its own sequence cursor after reconnecting.
- `lib/taskProjection.ts`: validates run events and projects task state and page previews.
- `lib/taskRoutes.ts`: canonical HTTP route construction for create/list/get/download operations.

Each submit creates a distinct thread and run, so several PPTs can generate concurrently. HTTP remains the control/data plane for create, list, snapshots, cancellation fallback, and artifacts; live snapshots, events, and primary cancellation use the single multiplexed WebSocket. A terminal run leaves the socket open for other runs, while selecting a restored terminal run can replay its history without creating another connection.

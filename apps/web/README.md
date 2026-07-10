# Web application

Vue 3 + TypeScript + Vite frontend for Agent Task Platform.

## Commands

```bash
npm install
npm run dev
npm test
npm run build
```

The development server proxies `/api/*` to `http://127.0.0.1:8080/api/*` without rewriting the path. All JSON calls use the canonical `/api/v1` API.

## Source layout

- `src/pages`: route-level adapters. Pages should remain thin.
- `src/features`: feature-owned UI, API maps, domain types, composables, and utilities.
- `src/shared`: cross-feature infrastructure such as the HTTP and SSE transports.
- `src/components`: reusable application-level UI components.
- `src/stores`: application state and API composition.

The shared HTTP client unwraps successful `{ data, meta }` responses and raises `ApiError` for `{ error: { code, message, details, request_id } }`. SSE streams and artifact downloads intentionally bypass the JSON envelope.

### Presentation feature

`src/features/presentations` is split by responsibility:

- `api.ts` and `types.ts`: public feature contract.
- `components/PresentationWorkspace.vue`: lightweight UI orchestration.
- `components/PresentationThread.vue`, `PresentationComposer.vue`, and history/preview components: focused UI sections.
- `composables/usePresentationConversations.ts`: history loading and hydration.
- `composables/usePresentationJobController.ts`: job lifecycle, actions, cancellation, and SSE reconnection.
- `composables/usePresentationMessages.ts`: reactive message store and event application.
- `lib/conversationProjection.ts`, `eventProjection.ts`, and `progressView.ts`: pure normalization and view projection functions.

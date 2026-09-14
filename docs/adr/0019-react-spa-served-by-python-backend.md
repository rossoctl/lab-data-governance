# React SPA is built by Vite and served by the Python backend, not nginx

The data-governance UI moves from hand-written HTML + vanilla JS (served as
static files by the Starlette backend under `/ui/`) to a React 18 + TypeScript
single-page app built with Vite, adopting the platform's `rossoctl/ui-v2`
stack: PatternFly, TanStack Query, React Router, and `@xyflow/react` + dagre
for graph views.

The platform's `ui-v2` splits the UI into its **own container**: a multi-stage
Docker build compiles the React app, then an nginx image serves the static
`dist/` and reverse-proxies `/api/` to a separate backend service. This ADR
**declines that topology** for data-governance. Instead, Vite's `dist/` is
baked into the existing single image and served by the same Starlette process
that serves `/api/` — one image, one Deployment, one origin, no nginx.

## Why

- **data-governance already serves its UI from the backend, and the manifests
  pin a single-image contract.** ADR (issue #38) established one Containerfile
  producing one image that the receiver, UI, and interactions Deployments all
  run, differing only by `command:`. `tests/deploy/test_manifests.py` asserts
  every Deployment runs `data-governance/receiver:latest`. A separate nginx
  image would break that tested invariant and add a second image to
  build-and-load. Serving the SPA from the backend keeps the topology the repo
  already committed to.
- **Same origin removes the reasons ui-v2 needs a proxy.** ui-v2's nginx
  exists largely to reverse-proxy `/api/` to a *different* service and to
  terminate its own routing. data-governance's `/api/` and `/ui/` are the same
  process, so there is no CORS boundary, no proxy hop, and no second network
  identity to manage. The Istio Gateway (`dg.localtest.me:8080`) already
  fronts the one Service.
- **Smallest infra delta for a single-team internal tool.** The scale that
  justifies a dedicated static-asset tier in the platform UI does not apply
  here. One Deployment is less to deploy, probe, and reason about.

## Considered alternatives

- **Mirror `ui-v2` exactly: separate nginx container proxying `/api/`.**
  Viable and maximally consistent with the platform. Rejected: it breaks the
  tested single-image contract (`test_manifests.py`, `test_image_build.py`),
  doubles the images build-and-load must handle, and buys nothing here —
  there is no cross-service `/api/` to proxy and no separate-origin problem
  to solve. Consistency with `ui-v2`'s *look and stack* is kept; only its
  *deployment topology* is declined.
- **Point `_UI_DIR` at a dedicated non-package location and split a UI-only
  image.** Leaner receiver/interactions pods (no unused UI assets), but the
  same single-image-contract churn as the nginx split for a KB-scale saving.
  Rejected.

## Consequences

- **The one Containerfile grows a Node build stage.** A `node`-based stage
  runs `npm ci && npm run build` over `data-governance/ui/`; the runtime stage
  copies the resulting `dist/` into `data_governance/api/ui/` so `_UI_DIR` is
  unchanged. The receiver and interactions pods carry the built UI assets they
  never serve (a few hundred KB) — accepted as negligible to preserve the
  single-image contract.
- **`_UI_DIR` now holds Vite build output, not source.** The hand-written
  `index.html`, `trace_tree.html`, `recent_traces_logic.js`,
  `trace_tree_logic.js`, and `execution_flow_logic.js` are deleted from source
  control. React source lives at `data-governance/ui/` (mirroring
  `rossoctl/ui-v2`); `data_governance/api/ui/` becomes a build-output
  target.
- **`/ui/` routing is restructured, `/api/` is untouched.** ADR-0017's
  `/api/`-JSON / `/ui/`-pages+assets namespacing is preserved: `/` still 302s
  to `/ui/`, React Router uses `basename="/ui"`, and deep links like
  `/ui/traces/{tid}` still resolve. The `_UI_ASSETS` frozenset whitelist and
  the `_ui_handler` / `_trace_tree_handler` / `_ui_asset_handler` routes are
  replaced by a `StaticFiles` mount at `/ui/assets` (Vite's content-hashed
  bundles) plus a catch-all `/ui/{path:path}` handler that returns
  `index.html` for client-side routing. The whitelist is retired because Vite
  emits content-hashed filenames that cannot be enumerated ahead of time.
- **UI-logic test coverage moves from Python to the JS toolchain.** The
  `node`-driven `test_recent_traces_logic_js.py` / `test_trace_tree_logic_js.py`
  are deleted; their logic (dedupe, missing-parent filter toggle,
  `descendantErrorAncestors`) is ported to TS and covered by Vitest. The
  HTML-string assertions inside `test_refresh_button.py` /
  `test_detail_panel_sections.py` are trimmed (the served shell is now a React
  bundle); their API-boundary assertions and all other `tests/api/` wire tests
  are kept. Playwright covers rendered views and deep links, mirroring
  `ui-v2`'s Vitest + Playwright split.
- **Authentication is explicitly out of scope.** Unlike `ui-v2`
  (keycloak-js + `ProtectedRoute`), the React UI keeps data-governance's
  current open-behind-the-Gateway posture. No `AuthContext`, no token
  validation on `/api/`. Auth, if ever wanted, is a separate ADR.
- **The cutover is big-bang, in one branch/PR.** The React app ships all
  three ported views (Recent Traces, span tree, flow tables) plus the new
  React Flow + dagre graph view in a single migration that deletes the
  vanilla JS/HTML and the dead node-JS tests together. No half-migrated
  state where React and vanilla views coexist; the review surface is large
  but the history is clean.

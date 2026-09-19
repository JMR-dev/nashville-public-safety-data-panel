# Frontend design and implementation handoff

Updated September 19, 2026. This records the agreed direction, proposed visual treatment, and the actual implementation checkpoint. The application is not complete or ready to deploy.

## Status after the handoff

Everything from "Present in the repository" onwards describes the repository as it stood at the handoff, not as it stands now. Since then, and as of September 19, 2026: the backend gate passes with 100% line and branch coverage; the frontend is implemented and tested with 100% statements, branches, functions, and lines; the GraphQL API, event stream, health checks, ingestion worker, CLI, and backups exist; the Bruno OpenCollection YAML collections in [`api-tests/`](../api-tests) and the Playwright flows in [`e2e/`](../e2e) run against the fixture-backed API in [`backend/tests/fixture_api.py`](../backend/tests/fixture_api.py). Still outstanding: containers, Quadlets, Ansible, the reverse proxy, CI, and operational documentation.

## Product direction

The first release is a public, read-only **live activity monitor for Nashville police calls for service**. It should help someone understand recently published calls, their approximate locations, and the source's freshness without needing to understand dispatch codes or ArcGIS.

Start with the active current-year dataset and retain collected history. Additional public safety sources are a later extension. Preserve upstream records and field names in storage and API source types; use readable labels in the interface. A call for service must not be presented as a confirmed crime or an emergency known to be active now.

## Frontend design thoughts

These are implementation recommendations, not completed mockups or an approved visual design system.

- **Hierarchy:** a compact title and source-status header, shared filters, a small activity summary, then the calls feed and map. The feed is the primary reading experience; the map supplies geographic context. Avoid making charts dominate the monitor.
- **Desktop:** place the scrollable newest-first feed beside a clustered map. Selecting either a feed row or map marker opens the same call details and highlights the corresponding location when available.
- **Mobile:** stack the controls and give the feed priority, with a clear way to switch to the map. Keep details and essential freshness information usable without a wide table.
- **Visual treatment:** a restrained civic-information interface with neutral surfaces, strong text contrast, and one primary accent. Reserve warning colors for source problems or stale data; do not assign alarming colors to calls based solely on dispatch descriptions. Use readable typography and compact but comfortably selectable rows.
- **Feed rows:** emphasize the call description, received time, approximate block/street, and area. Show disposition where available. Keep raw identifiers and less frequently used source attributes in the detail view.
- **Map:** use Leaflet with clustering, configurable tiles, and visible attribution. The feed must remain useful when tiles cannot load. Calls without usable coordinates stay in the feed and counts; they are not silently discarded.
- **Filters:** shared date range, zone, sector, call type, and disposition. Start with the preceding 24 hours. Apply the same filter meaning to feed, map, and summaries. Distinguish bounded map results from the total number of matching calls.
- **Lightweight summaries:** published-call count and call-type breakdown for the selected period. Define counts as source records unless a separately validated event-level metric is introduced; `Event_Number` is not assumed unique.
- **Accessibility:** keyboard-operable controls and call selection, visible focus, meaningful labels, text alongside status colors, and an accessible feed alternative to the map. Announce update availability without continuously reading every arriving call.

### Live updates and source honesty

The intended browser flow is GraphQL reads through TanStack Query, with `/events` server-sent notifications telling clients when committed data changes. Notifications are coalesced to at most once per second; clients refetch relevant queries and refresh after reconnecting.

Preserve the reader's position when calls arrive. Show an update indicator and provide pause/resume rather than automatically moving the list. Pause applies to displayed feed updates; it must not stop the server's ingestion worker.

Display successful polling separately from actual source freshness. A successful request does not establish that Nashville published new records. Expose last successful poll, last observed change, latest call timestamp, upstream edit time when available, and backfill progress. Distinguish initial loading, no matching calls, backfill in progress, disconnected browser updates, and degraded upstream ingestion. Retained data should remain usable during an outage.

Display times in `America/Chicago`, with clear time-zone labeling and daylight-saving handling. Locations are approximate upstream locations; do not imply more precision than the source provides.

## Planned stack and contracts

| Area | Agreed direction |
| --- | --- |
| Frontend | Vite, React, TypeScript, Node 26, pnpm |
| Data access and map | TanStack Query, Leaflet/react-leaflet |
| Frontend quality | ESLint with `n`, `unicorn`, and `security`; consult before rule tuning or suppressions |
| Backend | Python 3.14, uv, FastAPI, Strawberry, SQLAlchemy/Alembic, SQLite WAL |
| API | Read-only `/graphql`, `/events`, liveness and readiness endpoints |
| Tests | Vitest/RTL, pytest, Playwright, Bruno OpenCollection YAML |
| Deployment | AlmaLinux 10 guest, Debian slim app images, Podman Quadlets, Caddy with Coraza and Google Cloud DNS |
| Ownership | Ansible configures guests, nftables, fail2ban, and services; Pulumi provisions future hosting |

GraphQL is intended to expose paginated calls, call details, bounded map results, summaries, filter values, and source status. Source fields retain their upstream names, including `ZONE_`; computed presentation fields are separate. None of these HTTP interfaces has been implemented yet.

Ingestion is separate from browser refreshes. The agreed ongoing cadence is a 500 ms gap after each upstream response. Backfill starts under a 100 request-starts/second ceiling with at most 32 concurrent requests, reducing the rate on transient failures. Each request has five total attempts, honoring `Retry-After` or exponential backoff with jitter. Do not load-test the public source.

## Where implementation stopped

The repository was clean at commit `8ab3710` (`bootstrap`) before this handoff document was added.

### Present in the repository

- Root pnpm workspace, Node/Python version files, package manifests, `pnpm-lock.yaml`, `uv.lock`, and project requirements in [AGENTS.md](../AGENTS.md).
- Node is pinned to 26.9.0, pnpm to 12.4.2, and Python to 3.14.7. These were selected during the September 16 bootstrap; check current stable releases when resuming work.
- `frontend/package.json` declares React, React DOM, TanStack Query, Leaflet, and react-leaflet, plus intended development/build/test commands. The root manifest declares ESLint/plugins, TypeScript, Playwright, and Bruno CLI.
- Initial backend modules implement ArcGIS requests, pacing/retries, range partitioning, source-preserving tables, SQLite persistence, transactional page checkpoints, query helpers, and backup support. An initial Alembic migration and six tests exist. These are partial building blocks, not a functioning ingestion service.
- Python configuration enables Ruff, strict Pyright, and a 100% coverage gate including migrations.

### Exact interruption point

The dependency bootstrap encountered pnpm's build-script approval configuration for the Bruno transitive dependency `protobufjs`. `pnpm-workspace.yaml` now has `allowBuilds` entries for `protobufjs` and `esbuild`. The follow-up frontend development-dependency installation was interrupted and has not been verified as successful.

The frontend manifest still lacks Vite, the React Vite plugin, Vitest/coverage, React Testing Library, jsdom, and React/Leaflet type packages. There is no `index.html`, frontend source tree, CSS, TypeScript configuration, Vite configuration, ESLint configuration, or frontend test suite. Existing commands are scaffold declarations, not validated working workflows.

Also absent: FastAPI/Strawberry application, SSE implementation, worker orchestration, reconciliation scheduling, CLI implementation, Bruno collections, Playwright configuration/tests, CI, container definitions, Quadlets, Ansible, and infrastructure documentation. The declared `panel` CLI entry point references `panel.cli:main`, which does not exist yet.

### Verification on September 19

| Check | Observed result |
| --- | --- |
| `.venv/bin/pytest` | Six tests passed, but the command failed its coverage gate: **77.34% combined line/branch coverage**, required 100%. |
| `.venv/bin/ruff check backend` | Six findings: import formatting, one long line, and `zip()` missing an explicit `strict` argument. |
| `.venv/bin/ruff format --check backend` | Five files require formatting. |
| `.venv/bin/pyright` | Four errors: three `panel.tables` import/type-resolution findings and one partially unknown SQLAlchemy column type. |
| Frontend, Bruno, Playwright, containers, guest integration | Not yet implemented or verified. |

The six tests exercise the live response gap, retry limit/`Retry-After`, permanent ArcGIS errors, range partitioning, persisted checkpoints/changed records, and repeated event numbers. They do not constitute full ingestion or application validation. No coverage exclusions or lint-rule relaxations have been approved.

## Resume sequence

1. Finish dependency installation and verify lockfiles/toolchains. Resolve compatibility findings explicitly; do not silently downgrade packages or weaken checks.
2. Fix the existing backend lint/type issues and add meaningful tests for uncovered behavior. Inspect all current helpers before integrating them into services.
3. Establish frontend TypeScript/Vite, ESLint, Vitest/RTL, and full-source coverage configuration. Write behavior tests before implementing the initial shell, feed, filters, status, details, and map.
4. Define and test GraphQL contracts against fixture-backed SQLite, then connect the UI. Add SSE/reconnect behavior, source status, and the ingestion/reconciliation worker with deterministic upstream simulations.
5. Add executable Bruno OpenCollection YAML contracts and Playwright flows, then containers, Quadlets, Ansible, and their integration checks.
6. Complete CI and operational documentation only with honest reporting of outstanding checks.

No task is complete until its tests pass. Require 100% frontend statements/branches/functions/lines and 100% Python lines/branches across authored code, plus traceable infrastructure/API scenarios. Do not manufacture assertions to satisfy metrics. Stop and consult the user if code appears unreachable or an exclusion, ignore directive, or coverage exception is needed.

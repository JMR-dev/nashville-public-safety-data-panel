# Nashville Public Safety Panel

## Summary and engineering standards

Build a public, read-only police calls activity monitor using Vite/React, FastAPI/Strawberry, and SQLite. Start with the current active Nashville dataset, retain collected history, and support future source adapters.

Deliver local Podman Quadlets targeting AlmaLinux 10. Ansible owns guest configuration; provider-specific Pulumi provisioning remains deferred.

- **Frontend:** TypeScript, Node.js 26, pnpm, ESLint with `n`, `unicorn`, and `security` plugins.
- **Backend:** Python 3.14, uv, SQLAlchemy/Alembic, Ruff linting/formatting, and strict Pyright.
- **Testing:** Vitest/React Testing Library, pytest, Playwright, and Bruno HTTP collections in OpenCollection YAML.
- Use the latest stable packages available at implementation time unless a documented, compelling compatibility reason prevents it. Pin toolchains, commit pnpm/uv lockfiles, and use frozen installs.
- Start with recommended ESLint presets and browser/Node file scopes. Consult the user before rule tuning, severity reductions, or suppressions.
- Follow TDD wherever practical: failing behavior test, implementation, then refactoring.
- Record these standards in repository contributor/agent instructions and enforce them in CI.

## Coverage and completion requirements

- Require **100% statements, branches, functions, and lines for authored frontend code**, and **100% lines and branches for authored Python code**.
- Include all authored application modules in coverage collection, including unimported modules. Combine compatible unit and integration coverage; collect backend coverage during API integration tests.
- Require explicit, traceable scenario coverage for infrastructure, deployment configuration, and API collections, which do not have equivalent numerical metrics.
- Tests must verify meaningful behavior, contracts, or failure handling. Do not add tautological assertions, artificial execution paths, or unrealistic mocks solely to increase coverage.
- **Stop and consult the user when code appears unreachable or when any coverage exclusion, ignore directive, or threshold exception seems necessary.** Explain the code path, why it appears unreachable, and whether removal, refactoring, or an exception is appropriate.
- Do not silently remove files from instrumentation or change coverage denominators. Make the coverage scope visible and reviewable.
- No task is complete until its required tests pass and coverage requirements are met. Blocked, skipped, or failing required tests leave the task incomplete.

## Data and ingestion

- Use `docs/api_guide.md` as a reference and verify it against upstream metadata. Preserve source attribute names, types, nullability, and values in source-specific tables.
- Ingest all attributes without geometry. Preserve unexpected attributes in raw JSON; stop affected ingestion on incompatible schema changes.
- Namespace identity by source, dataset generation, and `OBJECTID`. Do not assume `Event_Number` is unique or identifiers survive source replacement. Keep provenance, schema snapshots, ingestion timestamps, and checkpoints separate.
- Preserve timestamps losslessly, expose UTC through GraphQL, and display America/Chicago time.
- Use SQLite WAL, indexed filter columns, a busy timeout, and one writer. Run ingestion separately from FastAPI using the same backend image and shared database directory.
- Backfill a captured upper `OBJECTID` boundary through disjoint, resumable ranges, using keyset pagination within each range. Default to 2,000 records per request, respecting advertised limits.
- Start backfill with a shared **100 request starts/second ceiling**, **32 concurrent requests**, and a bounded writer queue.
- Commit each page and checkpoint atomically. Complete the backfill boundary only when every range succeeds.
- Once caught up, poll continuously with a **500 ms gap after each response**, including empty responses. Browser activity must not multiply upstream polling.
- Permit **five total attempts per request** for transient network failures, HTTP 408/429/500/502/503/504, and equivalent ArcGIS errors. Honor valid `Retry-After`; otherwise use exponential backoff starting at two seconds, capped at 60 seconds, with jitter.
- On transient backfill failure, pause dispatch globally and halve the rate, down to one request/second. Retain the reduced rate for the run. After exhaustion, preserve checkpoints, expose degraded status, and wait at least 60 seconds before resuming.
- Reconcile the previous 48 hours every five minutes and the active dataset nightly. Prioritize live polling; reconciliation uses the ongoing polling gap.
- Infer removals only after complete successful reconciliation. Retain historical records with source-presence metadata; create new generations for rollover or detected identifier resets.
- Expose successful polling, actual record changes, latest call timestamp, upstream edit time when available, and backfill progress separately.

## Dashboard and interfaces

- Lead with a newest-first calls feed and clustered Leaflet map. Include details, activity counts, call-type breakdowns, and date/zone/sector/type/disposition filters.
- Default to the previous 24 hours. Support pausing updates, preserve reading position, and indicate new arrivals without unexpectedly moving the list.
- Keep calls without coordinates in tables and counts. Identify locations as approximate; distinguish published calls from confirmed crimes or active emergencies.
- Use TanStack Query for data access and configurable map tiles with attribution.
- Expose read-only Strawberry queries at `/graphql` for cursor-paginated calls, details, bounded map results, summaries, filter values, and source status. Preserve upstream names on source record fields; distinguish derived fields.
- Expose `/events` server-sent data-version notifications, coalesced to once per second. Refetch affected queries on notification and refresh after reconnecting.
- Provide liveness/readiness endpoints and enforce pagination, date-range, map-result, GraphQL-depth, and complexity limits.
- Maintain Bruno OpenCollection YAML tests covering GraphQL operations, health/readiness, validation failures, and event-stream HTTP headers. Verify streaming delivery and reconnect behavior through pytest and Playwright.

## Deployment and operations

- Build React with Node 26/pnpm and serve compiled assets through an unprivileged static server on Debian slim. Build the backend with Python 3.14/uv on Debian slim.
- Build pinned Caddy with Coraza and Google Cloud DNS modules. Enable OWASP CRS and route frontend, GraphQL, and event traffic through Caddy.
- Use system Quadlets and non-root application processes. Give Caddy host networking and bind application ports to loopback to preserve client IPs.
- Ansible configures AlmaLinux 10, Podman, Quadlets, enforcing SELinux volume access, nftables, fail2ban, log rotation, and backup timers.
- Make nftables the firewall owner, explicitly handling transition from firewalld. Allow web traffic and restrict SSH to a configured management CIDR.
- Feed trusted Coraza audit events into fail2ban, enforcing bans through nftables. Default to five WAF-denied requests in ten minutes and a one-hour ban. Ignore untrusted forwarding headers for client identification.
- Default to local Caddy TLS. Provide optional Google Cloud DNS ACME configuration with credentials outside source control.
- Back up SQLite daily through its online backup mechanism, retain seven local backups, and document restoration.
- Pulumi will own infrastructure provisioning and inventory outputs; Ansible owns guest setup. Cloud resources and remote backups remain deferred.

## Validation and acceptance

- **Vitest/RTL:** filters, feed controls, new-call indicators, loading/error/empty states, missing coordinates, and timestamp presentation.
- **pytest:** source mapping, pagination gaps, concurrent range completion, checkpoints, crash recovery, changed records, reconciliation, schema changes, rollover, and SQLite concurrency.
- **Simulated upstream:** pacing, concurrency bounds, global cooldown, `Retry-After`, error envelopes, and exactly five attempts. Do not load-test the public API at 100 requests/second.
- **Bruno:** execute collections against a running fixture-backed API, asserting response contracts and GraphQL errors as well as status codes.
- **Playwright:** full application flows, filtering, updates, reconnects, degraded ingestion, and responsive layouts.
- **AlmaLinux integration:** Ansible idempotence, reboot recovery, SELinux access, persistence, backup restoration, TLS, and Coraza → fail2ban → nftables enforcement for IPv4 and IPv6.
- CI gates completion on linting, formatting, type checking, required tests, 100% numerical coverage, and production builds. Deployment changes additionally require guest integration checks.
- Accept the release when all gates pass, backfill resumes reliably, committed calls reach connected dashboards within five seconds, and upstream outages leave retained data usable with clear status.

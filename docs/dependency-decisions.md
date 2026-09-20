# Dependency and toolchain decisions

Project policy is to use the latest stable releases unless a documented compatibility issue requires otherwise. Each deviation below states the reason and the condition for revisiting it.

## Node.js runtime

Node is pinned to 26.9.0 through `devEngines.runtime` in the root `package.json`. pnpm downloads that runtime, records it in `pnpm-lock.yaml`, and uses it for every `pnpm run` and `pnpm exec` in the workspace, regardless of the Node version installed on the host. `.node-version` carries the same value for editors and CI setup actions.

## TypeScript 6.0.3 instead of 7.x

TypeScript 7.0.2 is the current `latest` release. It is the native (Go) compiler and exposes only an unstable JavaScript API. typescript-eslint, which ESLint needs to parse TypeScript, requires `typescript >=4.8.4 <6.1.0` in both its latest release (8.70.0) and its canary builds (checked September 19, 2026). With TypeScript 7 installed, pnpm reports an unmet peer dependency and the parser cannot load the compiler API.

The workspace therefore uses TypeScript 6.0.3, the newest stable release inside the supported range, for both type checking and linting. Revisit this when a stable typescript-eslint release supports TypeScript 7.

## Supply-chain release-age gate

pnpm 12 refuses to install package versions published within its minimum release age. Two versions that were `latest` on September 19, 2026 fell inside that window: `eslint-plugin-unicorn@76.0.0` and `@types/node@26.6.2`. `pnpm add` added `minimumReleaseAgeExclude` entries for them automatically. Those exclusions were removed so the gate stays intact, and the lockfile was re-resolved from scratch under the gate. As a result:

- `eslint-plugin-unicorn` is at 75.0.0.
- `@types/node` is at 26.6.1.

Update both in a later dependency refresh, once the newer versions clear the gate. Do not add exclusions to get them early.

## Build-script approvals

`pnpm-workspace.yaml` allows lifecycle scripts only for `protobufjs`, a transitive dependency of the Bruno CLI. The bootstrap also allowed `esbuild`. Vite 8 uses Rolldown and no longer depends on esbuild, so that approval was removed.

## uvloop and its Python 3.14 deprecation warning

The API runs Uvicorn with its `standard` extra (uvloop, httptools, websockets, watchfiles). The tests run on the same uvloop event loop and httptools parser as production. uvloop 0.22.1, the newest release as of September 19, 2026, calls `asyncio.iscoroutinefunction`. Python 3.14 deprecates that function and plans to remove it in Python 3.16. For now each call still works but emits a `DeprecationWarning`. Python hides these warnings by default outside `__main__`, so production is unaffected. The test suite turns every warning into an error, so it failed.

The project owner approved an exception on September 19, 2026, after comparing it with running tests or production on the standard `asyncio` loop. `pyproject.toml` ignores exactly this warning message. Python attributes the warning to the code that calls `run_in_executor` rather than to uvloop, so the filter matches on the message instead of the module. Strict Pyright still reports any use of deprecated APIs in this project's own code.

Revisit this before adopting Python 3.16, or as soon as a uvloop release stops calling the deprecated function. Then remove the filter.

## Libraries not adopted

- `graphql-request` peers `graphql` 14–16, but `graphql` is at 17. The frontend calls GraphQL with `fetch` inside TanStack Query instead.
- `leaflet.markercluster` is used through `react-leaflet-cluster` 4.x, which declares peer support for `react-leaflet` 5 and React 19.

## Approved lint exception

`unicorn/no-null` is turned off for `frontend/test/**` only, approved by the project owner on September 19, 2026. The GraphQL API returns JSON `null` for upstream fields Metro Nashville did not publish. Test fixtures reproduce those responses exactly, and tests check how the interface presents missing values. Application code is still subject to the rule; it only compares values against `null`.


`@types/node` 26.6.1 is also a root development dependency, the same age-gated version the
frontend workspace pins, so that the Playwright configuration and the end-to-end flows are
type-checked by `tsconfig.json` at the root.

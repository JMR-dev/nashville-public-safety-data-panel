# Project requirements

Use Node 26 with pnpm and Python 3.14 with uv. Use the latest stable dependencies unless a documented compatibility issue requires otherwise. Commit lockfiles and use frozen installs.

Use TDD where practical. No task is complete until required tests pass. Frontend: Vitest/RTL and 100% statements, branches, functions, lines across authored application code. Backend: pytest and 100% lines/branches across authored Python, combining unit and integration coverage. Playwright verifies end-to-end behavior. All HTTP surfaces have Bruno OpenCollection YAML collections. Infrastructure has explicit scenario coverage.

Tests must assert meaningful behavior. Never invent tests just to execute lines. Stop and consult the user about apparently unreachable code, exclusions, ignore directives, or coverage exceptions. Do not silently change coverage denominators.

Frontend uses ESLint with n, unicorn, security; consult the user before tuning rules, reducing severity, or adding suppressions. Backend uses Ruff and strict Pyright.

Use AlmaLinux 10 for guests, Debian slim for application containers, Podman Quadlets for services. Pulumi owns provisioning; Ansible owns guest configuration. Keep public APIs read-only and upstream field names intact. Never load-test Nashville's public API.

"""The container images and the Quadlet units that run them.

These scenarios build the images and start them, so they are slower than the rest of the suite
and are selected with ``-m containers`` rather than run by default.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.container_support import (
    CONTAINERS,
    DASHBOARD_USER,
    GENERATORS,
    PANEL_USER,
    QUADLETS,
    ROOT,
    backend_image,
    dashboard_image,
    podman,
    serve,
    volume,
    wait_for,
)

pytestmark = pytest.mark.deployment

__all__ = ["backend_image", "dashboard_image", "volume"]

# Images


def test_the_backend_image_runs_the_panel_command_as_a_user_that_is_not_root(
    backend_image: str,
) -> None:
    assert "migrate" in podman("run", "--rm", backend_image, "--help")
    identity = podman("run", "--rm", "--entrypoint", "id", backend_image).strip()
    assert "uid=10001(panel)" in identity
    assert "uid=0" not in identity


def test_the_backend_image_migrates_a_fresh_volume_and_then_serves_it(
    backend_image: str, volume: str
) -> None:
    migrated = podman(
        "run", "--rm", "--volume", f"{volume}:/var/lib/panel:z", backend_image, "migrate"
    )
    assert "/var/lib/panel/panel.sqlite is at revision" in migrated

    with serve(backend_image, 18101, user=PANEL_USER, mount=volume, listens_on=8001) as api:
        assert wait_for(f"{api.url}/healthz").json() == {"status": "ok"}
        readiness = httpx.get(f"{api.url}/readyz", timeout=5)
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "ready"


def test_the_api_reports_an_unmigrated_volume_as_not_ready(backend_image: str, volume: str) -> None:
    with serve(backend_image, 18102, user=PANEL_USER, mount=volume, listens_on=8001) as api:
        wait_for(f"{api.url}/healthz")
        readiness = httpx.get(f"{api.url}/readyz", timeout=5)
        assert readiness.status_code == 503
        assert "migrated" in readiness.json()["reason"]


def test_the_dashboard_image_serves_the_built_dashboard(dashboard_image: str) -> None:
    with serve(dashboard_image, 18103, user=DASHBOARD_USER, listens_on=8080) as dashboard:
        base = dashboard.url
        page = wait_for(f"{base}/")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert '<div id="root"></div>' in page.text

        asset = re.search(r'/assets/[^"]+\.js', page.text)
        assert asset is not None, page.text
        script = httpx.get(f"{base}{asset.group(0)}", timeout=5)
        assert script.status_code == 200
        assert script.headers["content-type"].startswith("application/javascript")
        # Asset names carry a content hash, so a cached copy can never be the wrong one.
        assert "immutable" in script.headers["cache-control"]

        # The dashboard is one page: an unknown path is a mistake, not a route.
        assert httpx.get(f"{base}/nope", timeout=5).status_code == 404


def test_the_dashboard_image_runs_as_a_user_that_is_not_root(dashboard_image: str) -> None:
    identity = podman("run", "--rm", "--entrypoint", "id", dashboard_image).strip()
    assert "uid=10002(dashboard)" in identity


def test_the_images_hold_no_build_tooling(backend_image: str, dashboard_image: str) -> None:
    """The build stages stay in the build: what ships is the application and its runtime."""
    backend = podman(
        "run", "--rm", "--entrypoint", "sh", backend_image, "-c", "command -v uv || true"
    )
    assert backend.strip() == ""
    dashboard = podman(
        "run", "--rm", "--entrypoint", "sh", dashboard_image, "-c", "command -v node pnpm || true"
    )
    assert dashboard.strip() == ""


def test_the_health_command_the_unit_configures_passes_against_a_running_api(
    backend_image: str, volume: str
) -> None:
    """The command systemd will run to decide the API is healthy has to work in the image."""
    podman("run", "--rm", "--volume", f"{volume}:/var/lib/panel:z", backend_image, "migrate")
    unit = (QUADLETS / "panel-api.container").read_text()
    health = next(
        line.removeprefix("HealthCmd=")
        for line in unit.splitlines()
        if line.startswith("HealthCmd=")
    )
    with serve(
        backend_image,
        18104,
        user=PANEL_USER,
        mount=volume,
        listens_on=8001,
        options=("--health-cmd", health),
    ) as api:
        wait_for(f"{api.url}/healthz")
        podman("healthcheck", "run", api.name)
        state = podman("inspect", "--format", "{{.State.Health.Status}}", api.name)
        assert state.strip() == "healthy"


def test_a_deployment_can_point_the_map_at_its_own_tiles(dashboard_image: str) -> None:
    """Tile servers are deployment-specific, so they are a build argument, not an edit."""
    tiles = "https://tiles.example.test/{z}/{x}/{y}.png"
    podman(
        "build",
        "--file",
        str(CONTAINERS / "dashboard.Containerfile"),
        "--build-arg",
        f"VITE_TILE_URL={tiles}",
        "--build-arg",
        "VITE_TILE_ATTRIBUTION=Tiles by the city",
        "--tag",
        "localhost/panel-dashboard:tiles",
        str(ROOT),
    )
    with serve(
        "localhost/panel-dashboard:tiles", 18105, user=DASHBOARD_USER, listens_on=8080
    ) as dashboard:
        page = wait_for(f"{dashboard.url}/")
        asset = re.search(r'/assets/[^"]+\.js', page.text)
        assert asset is not None
        script = httpx.get(f"{dashboard.url}{asset.group(0)}", timeout=5).text
        assert tiles in script
        assert "Tiles by the city" in script


# Quadlets


def generator() -> Path:
    for candidate in GENERATORS:
        if candidate.exists():
            return candidate
    raise AssertionError(f"No Quadlet generator found in {[str(path) for path in GENERATORS]}")


@pytest.fixture(scope="module")
def units() -> dict[str, str]:
    """The systemd units Quadlet generates from the committed files, by unit name."""
    assert shutil.which("podman"), "podman is required to generate the units"
    finished = subprocess.run(
        [str(generator()), "-dryrun"],
        capture_output=True,
        text=True,
        timeout=120,
        env={"QUADLET_UNIT_DIRS": str(QUADLETS), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    generated: dict[str, str] = {}
    name = ""
    for line in finished.stdout.splitlines():
        heading = re.fullmatch(r"---(\S+)---", line)
        if heading is not None:
            name = heading.group(1)
            generated[name] = ""
        elif name:
            generated[name] += f"{line}\n"
    return generated


def start_command(unit: str) -> str:
    """The podman command a generated unit runs, with argument separators normalised."""
    line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    return line.removeprefix("ExecStart=").replace("=", " ")


def test_every_committed_quadlet_generates_a_unit(units: dict[str, str]) -> None:
    written = {path.stem for path in QUADLETS.glob("*.container")}
    assert {f"{name}.service" for name in written} <= set(units)
    assert "panel-data-volume.service" in units


def test_the_application_ports_are_published_on_the_loopback_address_only(
    units: dict[str, str],
) -> None:
    """Caddy has the host's network and is the only way in, so it sees real client addresses."""
    assert "--publish 127.0.0.1:8001:8001" in start_command(units["panel-api.service"])
    assert "--publish 127.0.0.1:8080:8080" in start_command(units["panel-dashboard.service"])
    # The worker answers nothing; it only writes the database.
    assert "--publish" not in start_command(units["panel-worker.service"])


@pytest.mark.parametrize(
    ("service", "user"),
    [
        ("panel-api.service", "10001:10001"),
        ("panel-worker.service", "10001:10001"),
        ("panel-backup.service", "10001:10001"),
        ("panel-dashboard.service", "10002:10002"),
        ("panel-caddy.service", "10003:10003"),
    ],
)
def test_containers_run_as_the_unprivileged_user_their_image_creates(
    units: dict[str, str], service: str, user: str
) -> None:
    command = start_command(units[service])
    assert f"--user {user}" in command
    assert "--security-opt no-new-privileges" in command
    assert "--cap-drop all" in command
    assert "--read-only" in command


def test_the_proxy_is_the_only_container_on_the_host_network(units: dict[str, str]) -> None:
    """Caddy sees the client's own address, which is what the ban list is written from."""
    edge = start_command(units["panel-caddy.service"])
    assert "--network host" in edge
    assert "--user 10003:10003" in edge
    assert "--read-only" in edge
    assert "--cap-drop all" in edge
    # Ports 80 and 443 are privileged, and that is the only privilege it gets.
    assert "--cap-add net_bind_service" in edge.lower()
    for service in ("panel-api.service", "panel-dashboard.service", "panel-worker.service"):
        assert "--network host" not in start_command(units[service])
        assert "cap-add" not in start_command(units[service])


def test_the_certificates_outlive_the_proxy_and_stay_out_of_the_database(
    units: dict[str, str],
) -> None:
    edge = start_command(units["panel-caddy.service"])
    assert "-v panel-certificates:/var/lib/caddy:z" in edge
    assert "panel-data" not in edge
    # fail2ban reads the firewall's audit log from the host, so that one is a host path.
    assert "-v /var/log/caddy:/var/log/caddy:z" in edge


def test_the_worker_and_the_api_share_one_database_volume(units: dict[str, str]) -> None:
    mount = "-v panel-data:/var/lib/panel:z"
    assert mount in start_command(units["panel-worker.service"])
    assert mount in start_command(units["panel-api.service"])
    assert mount in start_command(units["panel-backup.service"])
    # The dashboard is static files and has no business reading the database.
    assert "panel-data" not in start_command(units["panel-dashboard.service"])


def test_the_services_come_back_after_a_failure_or_a_reboot(units: dict[str, str]) -> None:
    for service in ("panel-api.service", "panel-worker.service", "panel-dashboard.service"):
        assert "Restart=always" in units[service]
        assert "WantedBy=multi-user.target" in units[service]


def test_the_backup_runs_once_when_its_timer_fires(units: dict[str, str]) -> None:
    backup = units["panel-backup.service"]
    assert "Type=oneshot" in backup
    assert "Restart=always" not in backup
    timer = (QUADLETS / "panel-backup.timer").read_text()
    assert "Unit=panel-backup.service" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_the_units_name_the_images_the_containerfiles_build(units: dict[str, str]) -> None:
    images = {
        "panel-api.service": "localhost/panel-backend:latest",
        "panel-worker.service": "localhost/panel-backend:latest",
        "panel-backup.service": "localhost/panel-backend:latest",
        "panel-dashboard.service": "localhost/panel-dashboard:latest",
    }
    for service, image in images.items():
        assert image in start_command(units[service])


def test_the_api_reports_its_own_health_to_systemd(units: dict[str, str]) -> None:
    command = start_command(units["panel-api.service"])
    assert "--health-cmd" in command
    assert "healthz" in command


def inspect(image: str) -> dict[str, Any]:
    return json.loads(podman("image", "inspect", image))[0]


def test_the_backend_image_keeps_the_database_outside_the_container(backend_image: str) -> None:
    configuration: dict[str, Any] = inspect(backend_image)["Config"]
    volumes: Sequence[str] = list(configuration.get("Volumes") or {})
    assert volumes == ["/var/lib/panel"]
    assert configuration["Env"]
    assert any(value.startswith("PANEL_DATABASE=/var/lib/panel/") for value in configuration["Env"])

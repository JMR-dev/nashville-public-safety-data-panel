"""Running the project's images with podman, for the scenarios that need them."""

import subprocess
import time
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest

ROOT = Path(__file__).parents[2]
CONTAINERS = ROOT / "containers"
QUADLETS = CONTAINERS / "quadlet"
BACKEND_IMAGE = "localhost/panel-backend:test"
PANEL_USER = "10001:10001"
DASHBOARD_USER = "10002:10002"
DASHBOARD_IMAGE = "localhost/panel-dashboard:test"
# What the Quadlet units give their containers, so the images are exercised the way they run.
HARDENING = (
    "--read-only",
    "--tmpfs",
    "/tmp",
    "--cap-drop",
    "all",
    "--security-opt",
    "no-new-privileges",
)
GENERATORS = (
    Path("/usr/libexec/podman/quadlet"),
    Path("/usr/lib/podman/quadlet"),
    Path("/usr/lib/systemd/system-generators/podman-system-generator"),
)


def podman(*arguments: str, timeout: float = 1800) -> str:
    """Run podman, failing the test with its output if it fails."""
    finished = subprocess.run(
        ["podman", *arguments], capture_output=True, text=True, timeout=timeout, check=False
    )
    assert finished.returncode == 0, f"podman {' '.join(arguments)} failed:\n{finished.stderr}"
    return finished.stdout


def build(containerfile: str, image: str) -> str:
    podman("build", "--file", str(CONTAINERS / containerfile), "--tag", image, str(ROOT))
    return image


@pytest.fixture(scope="module")
def backend_image() -> str:
    return build("backend.Containerfile", BACKEND_IMAGE)


@pytest.fixture(scope="module")
def dashboard_image() -> str:
    return build("dashboard.Containerfile", DASHBOARD_IMAGE)


@pytest.fixture
def volume() -> Iterator[str]:
    name = f"panel-test-{time.monotonic_ns()}"
    podman("volume", "create", name)
    yield name
    podman("volume", "rm", "--force", name)


class Service(NamedTuple):
    name: str
    url: str


@contextmanager
def serve(
    image: str,
    port: int,
    *arguments: str,
    user: str,
    mount: str | None = None,
    listens_on: int | None = None,
    options: Sequence[str] = (),
    host_network: bool = False,
) -> Generator[Service]:
    """Run a container under the restrictions its Quadlet imposes, while the test uses it."""
    name = f"panel-test-{time.monotonic_ns()}"
    volume = [] if mount is None else ["--volume", f"{mount}:/var/lib/panel:z"]
    # A container on the host's network has nothing to publish: it is already there.
    network = (
        ["--network", "host"]
        if host_network
        else ["--publish", f"127.0.0.1:{port}:{listens_on or port}"]
    )
    podman(
        "run",
        "--detach",
        "--name",
        name,
        "--user",
        user,
        *HARDENING,
        *options,
        *network,
        *volume,
        image,
        *arguments,
    )
    try:
        yield Service(name, f"http://127.0.0.1:{port}")
    finally:
        podman("rm", "--force", "--time", "5", name)


def wait_for(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            return httpx.get(url, timeout=2, headers=headers)
        except httpx.HTTPError:
            time.sleep(0.1)
    raise TimeoutError(f"{url} never answered")

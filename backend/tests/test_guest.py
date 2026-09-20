"""Configuring the guest the panel runs on, against an AlmaLinux 10 host running systemd.

The host arrives with firewalld, the way AlmaLinux ships it, and has to end up with nftables
owning the firewall, a ban service reading the proxy's audit log, and the panel's units in
place. The scenario runs the playbook twice: once to configure the host, once to prove that
running it again changes nothing.

A container is not a virtual machine. It runs the real playbook against a real systemd, real
dnf, and a real nftables in its own network namespace, but it cannot be rebooted, it has no
certificates to renew, and SELinux belongs to the host it runs on, so those parts of the
deployment are verified by the run against a machine, not here.
"""

import json
import re
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

import pytest

from tests.container_support import ROOT, podman

pytestmark = pytest.mark.deployment

GUEST_IMAGE = "docker.io/library/almalinux:10"
GUEST = "panel-guest-scenario"
ANSIBLE = ROOT / "infra" / "ansible"
QUADLETS = ROOT / "containers" / "quadlet"
SITE = "panel.test"
MANAGEMENT_V4 = "198.51.100.0/24"
MANAGEMENT_V6 = "2001:db8:5::/48"


class Recap(NamedTuple):
    ok: int
    changed: int
    failed: int
    unreachable: int


def inside(*command: str) -> str:
    return podman("exec", GUEST, *command, timeout=600)


def attempt(*command: str) -> tuple[int, str]:
    """Run a command in the guest where a non-zero result is an answer, not a failure."""
    finished = subprocess.run(
        ["podman", "exec", GUEST, *command],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return finished.returncode, finished.stdout + finished.stderr


def boot() -> None:
    podman(
        "run",
        "--detach",
        "--name",
        GUEST,
        "--systemd=always",
        "--cap-add",
        "NET_ADMIN,NET_RAW",
        "--stop-signal",
        "SIGRTMIN+3",
        GUEST_IMAGE,
        "/usr/sbin/init",
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = subprocess.run(
            ["podman", "exec", GUEST, "systemctl", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout.strip()
        if state in {"running", "degraded"}:
            return
        time.sleep(0.5)
    raise TimeoutError("the guest never finished starting")


@pytest.fixture(scope="module")
def guest() -> Generator[str]:
    podman("rm", "--force", "--time", "1", GUEST)
    boot()
    try:
        # A host as AlmaLinux ships it: firewalld installed and running.
        inside("dnf", "--assumeyes", "--quiet", "install", "firewalld")
        inside("systemctl", "enable", "--now", "firewalld")
        yield GUEST
    finally:
        podman("rm", "--force", "--time", "5", GUEST)


def play(inventory: Path) -> Recap:
    finished = subprocess.run(
        ["ansible-playbook", "-i", str(inventory), "site.yml"],
        capture_output=True,
        text=True,
        timeout=1800,
        cwd=ANSIBLE,
        env={
            # This project's own ansible, not whatever the machine happens to have.
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
            "ANSIBLE_CONFIG": str(ANSIBLE / "ansible.cfg"),
            "ANSIBLE_FORCE_COLOR": "0",
        },
        check=False,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    counted = re.search(
        r"ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", finished.stdout
    )
    assert counted is not None, finished.stdout
    ok, changed, unreachable, failed = (int(value) for value in counted.groups())
    return Recap(ok=ok, changed=changed, failed=failed, unreachable=unreachable)


def describe(guest: str, **settings: str) -> str:
    lines = "".join(f"{name}={value}\n" for name, value in settings.items())
    return (
        f"[panel]\n{guest} ansible_connection=podman ansible_become=false\n\n"
        "[panel:vars]\n"
        f"panel_site_address={SITE}\n"
        f"panel_management_v4={{ {MANAGEMENT_V4} }}\n"
        f"panel_management_v6={{ {MANAGEMENT_V6} }}\n"
        # The images are built elsewhere, so this host is configured but not started.
        "panel_start_services=false\n" + lines
    )


@pytest.fixture(scope="module")
def inventory(guest: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("inventory") / "guest.ini"
    path.write_text(describe(guest))
    return path


@pytest.fixture(scope="module")
def configured(inventory: Path) -> tuple[Recap, Recap]:
    """The playbook run twice: configuring the host, then finding nothing left to do."""
    return play(inventory), play(inventory)


def test_the_playbook_configures_a_host_that_arrived_with_nothing(
    configured: tuple[Recap, Recap],
) -> None:
    first, _ = configured
    assert (first.failed, first.unreachable) == (0, 0)
    assert first.changed > 0


def test_running_it_again_leaves_the_host_alone(configured: tuple[Recap, Recap]) -> None:
    """A role that reports work on every run cannot tell a drifted host from a settled one."""
    _, second = configured
    assert second == Recap(ok=second.ok, changed=0, failed=0, unreachable=0)


def test_nftables_takes_the_firewall_over_from_firewalld(configured: tuple[Recap, Recap]) -> None:
    # A masked unit cannot be started by anything, including a package update.
    assert attempt("systemctl", "is-enabled", "firewalld")[1].strip() == "masked"
    assert attempt("systemctl", "is-active", "firewalld")[1].strip() == "inactive"
    assert inside("systemctl", "is-active", "nftables").strip() == "active"
    assert inside("systemctl", "is-enabled", "nftables").strip() == "enabled"


def test_the_host_loads_the_ruleset_this_repository_ships(
    configured: tuple[Recap, Recap],
) -> None:
    ruleset = inside("nft", "list", "ruleset")
    assert "table inet panel" in ruleset
    assert "type filter hook input priority filter; policy drop;" in ruleset
    assert "tcp dport { 80, 443 } accept" in ruleset
    # The addresses come from this deployment's inventory, not from the role's defaults.
    assert MANAGEMENT_V4 in ruleset
    assert MANAGEMENT_V6 in ruleset
    assert "203.0.113.0/24" not in ruleset


def test_the_host_refuses_what_it_was_not_asked_to_serve(configured: tuple[Recap, Recap]) -> None:
    ruleset = inside("nft", "list", "ruleset")
    assert "type filter hook forward priority filter; policy drop;" in ruleset
    assert "ct state established,related accept" in ruleset
    assert "ct state invalid drop" in ruleset
    # The application ports are on the loopback address, so they are never a rule here.
    for port in ("8001", "8080"):
        assert f"dport {port}" not in ruleset


def test_administration_is_accepted_only_from_known_addresses(
    configured: tuple[Recap, Recap],
) -> None:
    ruleset = inside("nft", "list", "ruleset")
    assert "ip saddr @management tcp dport 22 accept" in ruleset
    assert "ip6 saddr @management6 tcp dport 22 accept" in ruleset
    # Every rule that opens ssh names an address set: none of them opens it to everyone.
    ssh = [line.strip() for line in ruleset.splitlines() if "dport 22" in line]
    assert ssh
    assert all("@management" in rule for rule in ssh)


def test_the_worker_can_still_reach_the_source_it_ingests(
    configured: tuple[Recap, Recap],
) -> None:
    assert 'iifname "podman*" accept' in inside("nft", "list", "ruleset")


def test_diagnostics_and_path_discovery_keep_working(configured: tuple[Recap, Recap]) -> None:
    ruleset = inside("nft", "list", "ruleset")
    for kind in ("echo-request", "destination-unreachable", "time-exceeded"):
        assert kind in ruleset
    assert "packet-too-big" in ruleset


def test_the_firewall_survives_a_restart_of_its_service(configured: tuple[Recap, Recap]) -> None:
    """Rules that live only in memory are rules that disappear when something restarts."""
    inside("systemctl", "restart", "nftables")
    assert "table inet panel" in inside("nft", "list", "ruleset")


def test_the_ban_service_watches_what_the_web_firewall_writes(
    configured: tuple[Recap, Recap],
) -> None:
    assert inside("systemctl", "is-active", "fail2ban").strip() == "active"
    jail = inside("fail2ban-client", "status", "panel-waf")
    assert "/var/log/caddy/coraza-audit.log" in jail
    # The log exists before the proxy has written anything: fail2ban refuses to start without it.
    assert inside("test", "-f", "/var/log/caddy/coraza-audit.log") == ""


def test_the_ban_reaches_the_firewall(configured: tuple[Recap, Recap]) -> None:
    configuration = inside("fail2ban-client", "get", "panel-waf", "actions")
    assert "nftables" in configuration


def test_the_units_that_run_the_panel_are_installed(configured: tuple[Recap, Recap]) -> None:
    installed = inside("ls", "/etc/containers/systemd").split()
    expected = {path.name for path in QUADLETS.glob("*.container")} | {
        path.name for path in QUADLETS.glob("*.volume")
    }
    assert set(installed) == expected


def test_systemd_generates_services_from_those_units(configured: tuple[Recap, Recap]) -> None:
    """Quadlet turns the units into services at every daemon reload, and did here."""
    for service in ("panel-worker", "panel-api", "panel-dashboard", "panel-caddy"):
        unit = inside("systemctl", "cat", f"{service}.service")
        assert "Automatically generated by" in unit
        assert "podman run" in unit


def test_the_backup_runs_on_a_schedule(configured: tuple[Recap, Recap]) -> None:
    assert inside("systemctl", "is-enabled", "panel-backup.timer").strip() == "enabled"
    timer = inside("systemctl", "cat", "panel-backup.timer")
    assert "OnCalendar=*-*-* 04:30:00" in timer
    assert "Unit=panel-backup.service" in timer


def test_the_deployment_settings_reach_the_units(configured: tuple[Recap, Recap]) -> None:
    """What differs between deployments lives in a drop-in, not in the units themselves."""
    drop_in = inside("cat", "/etc/systemd/system/panel-caddy.service.d/deployment.conf")
    assert f"Environment=PANEL_SITE_ADDRESS={SITE}" in drop_in
    assert "Environment=PANEL_API_UPSTREAM=127.0.0.1:8001" in drop_in
    assert f"PANEL_SITE_ADDRESS={SITE}" in inside("systemctl", "show", "panel-caddy.service")


def test_the_proxy_can_write_where_the_ban_service_reads(configured: tuple[Recap, Recap]) -> None:
    listing = inside("stat", "--format=%u %g %a", "/var/log/caddy")
    assert listing.strip() == "10003 10003 750"


def test_the_logs_are_rotated_without_moving_the_file(configured: tuple[Recap, Recap]) -> None:
    """fail2ban follows an open file, so rotation copies and truncates instead of renaming."""
    configuration = inside("cat", "/etc/logrotate.d/panel-caddy")
    assert "copytruncate" in configuration
    # logrotate reads the configuration when asked what it would do, and says so if it cannot.
    code, output = attempt("logrotate", "--debug", "/etc/logrotate.d/panel-caddy")
    assert code == 0, output
    assert "error" not in output.lower(), output
    assert "/var/log/caddy/coraza-audit.log" in output


def test_the_host_keeps_no_secrets_from_this_repository() -> None:
    """Credentials belong to the deployment, so nothing here carries one."""
    for path in (ANSIBLE / "inventory").glob("*"):
        text = path.read_text()
        assert "password" not in text.lower()
        assert "BEGIN PRIVATE KEY" not in text
    playbook = json.dumps(list((ANSIBLE / "roles").rglob("*.yml")), default=str)
    assert "secret" not in playbook.lower()


# Certificates


def test_a_deployment_asks_for_no_certificate_by_itself(configured: tuple[Recap, Recap]) -> None:
    """Without a challenge configured, the proxy issues its own certificate."""
    assert inside("ls", "/etc/caddy/conf.d").split() == []


def test_a_deployment_can_prove_its_name_through_a_dns_challenge(
    guest: str,
    configured: tuple[Recap, Recap],
    inventory: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The credentials stay with whoever runs Ansible; the host gets a file only the proxy reads."""
    credentials = tmp_path_factory.mktemp("acme") / "dns-credentials.json"
    credentials.write_text(json.dumps({"type": "service_account", "project_id": "panel-example"}))
    with_challenge = inventory.parent / "guest-acme.ini"
    with_challenge.write_text(
        describe(
            guest,
            panel_acme_email="operations@example.org",
            panel_acme_gcp_project="panel-example",
            panel_acme_credentials=str(credentials),
        )
    )

    recap = play(with_challenge)
    assert (recap.failed, recap.unreachable) == (0, 0)
    assert recap.changed > 0

    written = inside("cat", "/etc/caddy/conf.d/tls.caddy")
    assert "dns googleclouddns" in written
    assert "gcp_project panel-example" in written
    assert "tls operations@example.org" in written
    # The credentials are readable by the proxy and by nobody else.
    assert inside("stat", "--format=%u %g %a", "/etc/caddy/acme/dns-credentials.json").strip() == (
        "0 10003 640"
    )

    # What the proxy would load has to be something the proxy accepts.
    staged = tmp_path_factory.mktemp("conf.d")
    podman("cp", f"{GUEST}:/etc/caddy/conf.d/tls.caddy", str(staged / "tls.caddy"))
    validation = podman(
        "run",
        "--rm",
        "--volume",
        f"{staged}:/etc/caddy/conf.d:ro,z",
        "--env",
        f"PANEL_SITE_ADDRESS={SITE}",
        "localhost/panel-caddy:test",
        "validate",
        "--config",
        "/etc/caddy/Caddyfile",
    )
    assert "Valid configuration" in validation


def test_taking_the_challenge_away_gives_certificates_back_to_the_proxy(
    configured: tuple[Recap, Recap], inventory: Path
) -> None:
    recap = play(inventory)
    assert recap.failed == 0
    assert inside("ls", "/etc/caddy/conf.d").split() == []
    assert inside("ls", "/etc/caddy/acme").split() == []

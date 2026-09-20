"""The public edge: the proxy, the rules it enforces, and what a refused request leads to.

Everything a browser sends arrives at Caddy, which runs the OWASP Core Rule Set in front of a
dashboard and an API that listen only on the loopback address. A web application firewall in
front of GraphQL is exactly the place where a deployment quietly breaks its own application, so
these scenarios send the operations the dashboard actually sends and check they get through,
then check that the attacks it is there to stop do not.
"""

import json
import re
import subprocess
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import pytest

from tests.container_support import (
    CONTAINERS,
    DASHBOARD_USER,
    ROOT,
    build,
    dashboard_image,
    serve,
    wait_for,
)
from tests.fixture_api import API_PORT, fixture_services

pytestmark = pytest.mark.deployment

__all__ = ["dashboard_image"]

CADDY_IMAGE = "localhost/panel-caddy:test"
CADDY_USER = "10003:10003"
EDGE_PORT = 18443
DASHBOARD_PORT = 18080
# Production has a domain name, and the Core Rule Set says so about a bare address.
SITE = "panel.test"
OPERATIONS = ROOT / "frontend" / "src" / "api" / "operations"
NFTABLES = ROOT / "infra" / "nftables"
FAIL2BAN = ROOT / "infra" / "fail2ban"
WINDOW = {"since": "2026-09-18T12:00:00+00:00", "until": "2026-09-19T12:00:00+00:00"}
# One set of variables serves every operation; each takes the ones it declares.
VARIABLES: dict[str, Any] = {
    "filter": WINDOW,
    "first": 50,
    "after": None,
    "asOfVersion": 2,
    "sinceVersion": 1,
    "types": 10,
    "limit": 4000,
    "since": WINDOW["since"],
    "until": WINDOW["until"],
    "id": "1:5",
}


class Edge(NamedTuple):
    url: str
    audit_log: Path


@pytest.fixture(scope="module")
def caddy_image() -> str:
    return build("caddy.Containerfile", CADDY_IMAGE)


@pytest.fixture(scope="module")
def edge(
    caddy_image: str, dashboard_image: str, tmp_path_factory: pytest.TempPathFactory
) -> Generator[Edge]:
    """The whole edge: the API and dashboard behind the proxy that the public reaches."""
    logs = tmp_path_factory.mktemp("caddy")
    logs.chmod(0o777)
    options = (
        # The proxy binds privileged ports in production, and the binary carries that
        # capability, so it cannot start without being allowed to have it.
        "--cap-add",
        "NET_BIND_SERVICE",
        "--tmpfs",
        "/var/lib/caddy",
        "--volume",
        f"{logs}:/var/log/caddy:z",
        "--env",
        f"PANEL_SITE_ADDRESS=http://:{EDGE_PORT}",
        "--env",
        f"PANEL_API_UPSTREAM=127.0.0.1:{API_PORT}",
        "--env",
        f"PANEL_DASHBOARD_UPSTREAM=127.0.0.1:{DASHBOARD_PORT}",
    )
    with (
        fixture_services(tmp_path_factory.mktemp("fixture")),
        serve(dashboard_image, DASHBOARD_PORT, user=DASHBOARD_USER, listens_on=8080),
        serve(caddy_image, EDGE_PORT, user=CADDY_USER, options=options, host_network=True),
    ):
        url = f"http://127.0.0.1:{EDGE_PORT}"
        wait_for(f"{url}/", headers={"Host": SITE})
        yield Edge(url, logs / "coraza-audit.log")


def ask(edge: Edge, path: str = "/", **arguments: Any) -> httpx.Response:
    return httpx.request(
        arguments.pop("method", "GET"),
        f"{edge.url}{path}",
        headers={"Host": SITE, **arguments.pop("headers", {})},
        timeout=10,
        **arguments,
    )


def operation(edge: Edge, name: str) -> httpx.Response:
    query = (OPERATIONS / f"{name}.graphql").read_text()
    return ask(
        edge,
        "/graphql",
        method="POST",
        json={"query": query, "variables": VARIABLES},
        headers={"content-type": "application/json"},
    )


# What the dashboard needs to work


def test_the_proxy_serves_the_dashboard(edge: Edge) -> None:
    page = ask(edge)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in page.text


@pytest.mark.parametrize(
    "name",
    ["anchor", "status", "feed", "new-calls", "summary", "map-calls", "filter-values", "detail"],
)
def test_every_operation_the_dashboard_sends_passes_the_firewall(edge: Edge, name: str) -> None:
    """A firewall that refuses the application's own requests is an outage with good intentions."""
    document = "call-detail" if name == "detail" else name
    response = operation(edge, document)
    assert response.status_code == 200, response.text
    assert "errors" not in response.json(), response.text


def test_paging_through_the_feed_passes_the_firewall(edge: Edge) -> None:
    """Cursors are opaque base64, which is the shape a firewall tends to find suspicious."""
    first = ask(
        edge,
        "/graphql",
        method="POST",
        headers={"content-type": "application/json"},
        json={
            "query": "query Feed($filter: CallFilter!) { calls(filter: $filter, first: 2) "
            "{ pageInfo { endCursor } nodes { id } } }",
            "variables": {"filter": WINDOW},
        },
    )
    cursor = first.json()["data"]["calls"]["pageInfo"]["endCursor"]
    assert cursor

    following = ask(
        edge,
        "/graphql",
        method="POST",
        headers={"content-type": "application/json"},
        json={
            "query": "query Feed($filter: CallFilter!, $after: String) "
            "{ calls(filter: $filter, first: 2, after: $after) { nodes { id } } }",
            "variables": {"filter": WINDOW, "after": cursor},
        },
    )
    assert following.status_code == 200, following.text
    assert len(following.json()["data"]["calls"]["nodes"]) == 2


def test_the_event_stream_reaches_the_browser_as_it_is_produced(edge: Edge) -> None:
    """Notifications are useless if the proxy holds them until a buffer fills."""
    with httpx.stream("GET", f"{edge.url}/events", headers={"Host": SITE}, timeout=15) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        frame = ""
        for chunk in response.iter_text():
            frame += chunk
            if frame.endswith("\n\n"):
                break
    assert "event: version" in frame
    assert "retry: 3000" in frame


def test_a_query_may_arrive_as_a_get(edge: Edge) -> None:
    response = ask(edge, "/graphql?query=%7BdataVersion%7D")
    assert response.status_code == 200
    assert response.json()["data"]["dataVersion"] == 2


def test_the_proxy_tells_the_browser_what_the_page_may_do(edge: Edge) -> None:
    headers = ask(edge).headers
    policy = headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    # The map's tiles come from somewhere else, and nothing else may.
    assert "img-src 'self' data: https://tile.openstreetmap.org" in policy
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert "server" not in headers


# What the firewall is there for


@pytest.mark.parametrize(
    ("description", "path"),
    [
        ("sql injection", "/graphql?query=1%20UNION%20SELECT%20*%20FROM%20users--"),
        ("cross-site scripting", "/?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"),
        ("shell command", "/?name=%3B%20cat%20%2Fetc%2Fpasswd"),
    ],
)
def test_attacks_are_refused_at_the_edge(edge: Edge, description: str, path: str) -> None:
    assert ask(edge, path).status_code == 403, description


def test_the_application_never_sees_a_refused_request(edge: Edge) -> None:
    """A refused request is stopped at the edge, so it costs the API nothing."""
    before = ask(edge, "/graphql?query=%7BdataVersion%7D").json()["data"]["dataVersion"]
    assert ask(edge, "/graphql?query=1%20UNION%20SELECT%20*%20FROM%20users--").status_code == 403
    after = ask(edge, "/graphql?query=%7BdataVersion%7D").json()["data"]["dataVersion"]
    assert before == after


def audited(edge: Edge) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in edge.audit_log.read_text().splitlines():
        transaction: dict[str, Any] = json.loads(line)["transaction"]
        entries.append(transaction)
    return entries


def test_a_refused_request_is_recorded_with_the_address_it_came_from(edge: Edge) -> None:
    ask(edge, "/graphql?query=1%20UNION%20SELECT%20*%20FROM%20users--")
    stopped = [entry for entry in audited(edge) if entry["is_interrupted"]]
    assert stopped
    # The address is the one the connection came from: no forwarding header is trusted, so a
    # client cannot get somebody else banned by claiming their address.
    assert {entry["client_ip"] for entry in stopped} == {"127.0.0.1"}


def scan(log: Path) -> str:
    report = subprocess.run(
        ["fail2ban-regex", str(log), str(FAIL2BAN / "filter.d" / "panel-waf.conf")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    return report.stdout


def test_the_ban_list_reads_what_the_firewall_records(edge: Edge) -> None:
    """The fail2ban filter has to match the log the firewall actually writes."""
    ask(edge, "/graphql?query=1%20UNION%20SELECT%20*%20FROM%20users--")
    ask(edge, "/?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E")

    report = scan(edge.audit_log)
    lines, matched = counted(report)
    stopped = [entry for entry in audited(edge) if entry["is_interrupted"]]
    assert matched == len(stopped) == lines
    # Bans are counted over ten minutes, so the times have to be read out of the log.
    assert "Year/Month/Day" in report


def test_a_request_the_firewall_let_through_earns_nobody_a_ban(edge: Edge, tmp_path: Path) -> None:
    """The same audited line, with only the field that decides it changed."""
    ask(edge, "/graphql?query=1%20UNION%20SELECT%20*%20FROM%20users--")
    stopped = edge.audit_log.read_text().splitlines()[-1]
    assert '"is_interrupted":true' in stopped

    noticed = tmp_path / "audit.log"
    noticed.write_text(stopped.replace('"is_interrupted":true', '"is_interrupted":false') + "\n")

    lines, matched = counted(scan(noticed))
    assert (lines, matched) == (1, 0)


def counted(report: str) -> tuple[int, int]:
    found = re.search(r"Lines: (\d+) lines, \d+ ignored, (\d+) matched", report)
    assert found is not None, report
    return int(found.group(1)), int(found.group(2))


def test_the_ban_is_the_one_the_deployment_documents() -> None:
    jail = (FAIL2BAN / "jail.d" / "panel-waf.local").read_text()
    settings = dict(
        line.split("=", 1) for line in jail.splitlines() if "=" in line and not line.startswith("#")
    )
    values = {key.strip(): value.strip() for key, value in settings.items()}
    assert values["maxretry"] == "5"
    assert values["findtime"] == "600"
    assert values["bantime"] == "3600"
    assert values["action"] == "nftables[type=allports]"
    assert values["logpath"] == "/var/log/caddy/coraza-audit.log"


# The host firewall
#
# The ruleset is loaded for real, on the platform it is written for, by the guest scenarios in
# test_guest.py: loading it needs privileges that a test runner does not necessarily have, and
# a host that merely parses it has not proved anything.


def test_the_committed_ruleset_expects_its_addresses_from_the_deployment() -> None:
    """The management addresses are the deployment's, so they are not in the ruleset itself."""
    ruleset = (NFTABLES / "panel.nft").read_text()
    assert 'include "/etc/nftables/panel-management.nft"' in ruleset
    assert "203.0.113" not in ruleset
    example = (NFTABLES / "panel-management.example.nft").read_text()
    assert "define management_v4" in example
    assert "define management_v6" in example


def test_the_proxy_carries_only_the_two_modules_it_needs(caddy_image: str) -> None:
    from tests.container_support import podman

    modules = podman("run", "--rm", "--entrypoint", "caddy", caddy_image, "list-modules")
    assert "dns.providers.googleclouddns" in modules
    assert "http.handlers.waf" in modules
    for unwanted in ("dns.providers.cloudflare", "dns.providers.route53"):
        assert unwanted not in modules


def test_the_rules_the_firewall_enforces_are_pinned(caddy_image: str) -> None:
    """A rule set that changes under a deployment is a deployment that changed without review."""
    containerfile = (CONTAINERS / "caddy.Containerfile").read_text()
    assert re.search(r"ARG CRS_SHA256=[0-9a-f]{64}", containerfile)
    assert re.search(r"ARG CORAZA_CONF_SHA256=[0-9a-f]{64}", containerfile)
    assert "sha256sum --check --strict" in containerfile


def test_the_exclusions_name_one_rule_each_and_say_why() -> None:
    """Every exclusion is a hole in the firewall, so each one is narrow and explained."""
    exclusions = (CONTAINERS / "caddy" / "coraza" / "panel.conf").read_text()
    removals: Sequence[str] = re.findall(r"ctl:ruleRemoveTargetById=(\d+);(\S+)", exclusions)
    assert removals
    for rule, target in removals:
        assert rule.isdecimal()
        # Scoped to one argument of one request, never the whole request.
        assert target.startswith("ARGS:")
    # Each is scoped to the API path rather than the whole site.
    assert exclusions.count('SecRule REQUEST_URI "@beginsWith /graphql"') == len(removals)
    assert "SecRuleEngine Off" not in exclusions

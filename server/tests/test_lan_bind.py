"""Listening on the LAN, and everything that has to move with it.

The switch is one boolean, and the ways it can be half-implemented are all silent: a socket on every
interface with the browser's origin still refused (a CORS error in a console nobody on a phone can
open), or a widened origin list on a socket that never left 127.0.0.1 (a fence held open in front of
a wall). So the three things that must agree are asserted together here — the address, the origins,
and what the script actually hands uvicorn.

None of this needs a database, which is the point: these are decisions made in `Settings.__init__`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.settings import LOCAL_ORIGINS, Settings

REPO = Path(__file__).resolve().parents[2]

#: Where a phone and a second laptop on a home or office network actually live.
PRIVATE = ["http://192.168.1.14:5180", "http://10.0.0.5:8787", "http://172.16.4.4:5180",
           "http://172.31.255.1", "http://rajat-mac.local:5180", "https://192.168.0.2"]
#: And the ones that must stay out, including the two that look private and are not.
ELSEWHERE = ["http://evil.example.com", "http://192.168.1.5.example.com", "http://172.32.0.1",
             "http://10.0.0.5.evil.com", "http://notreally.local.evil.com"]


def test_bound_to_this_machine_by_default() -> None:
    """Nothing about reachability changes for anyone who does not ask for it."""
    plain = Settings()
    assert plain.listen_on_lan is False
    assert plain.bind_host == "127.0.0.1"
    assert plain.cors_origin_regex == LOCAL_ORIGINS
    assert plain.reach_notice() == []


def test_the_switch_moves_the_socket_and_the_origins_together() -> None:
    open_to_the_network = Settings(listen_on_lan=True)
    assert open_to_the_network.bind_host == "0.0.0.0"  # noqa: S104 — asserting the switch did its job
    allowed = re.compile(open_to_the_network.cors_origin_regex)
    for origin in PRIVATE:
        assert allowed.match(origin), f"a phone at {origin} could not sign in"
    for origin in ELSEWHERE:
        assert not allowed.match(origin), f"{origin} was let in by the LAN origin rule"


def test_localhost_still_works_when_the_lan_is_open() -> None:
    """Opening it to the network must not close the door you were already using."""
    allowed = re.compile(Settings(listen_on_lan=True).cors_origin_regex)
    assert allowed.match("http://localhost:5180")
    assert allowed.match("http://127.0.0.1:8787")


def test_an_origin_rule_someone_wrote_is_never_widened() -> None:
    """Whoever set this has already said which origins they mean; adding to it would overrule them."""
    mine = r"^https://neurocode\.example\.com$"
    assert Settings(listen_on_lan=True, cors_origin_regex=mine).cors_origin_regex == mine


def test_the_notice_says_what_it_costs() -> None:
    said = Settings(listen_on_lan=True, machine_access=True).reach_notice()
    assert any("everyone on this network" in line for line in said)
    assert any("in the clear" in line for line in said)
    # The dangerous pair — reachable from the network *and* holding a shell on this machine — is
    # named on its own, with the setting that turns it off.
    shell = [line for line in said if "Workbench" in line]
    assert shell and "NEUROCODE_MACHINE_ACCESS=false" in shell[0]


def test_the_shell_warning_is_dropped_when_there_is_no_shell() -> None:
    said = Settings(listen_on_lan=True, machine_access=False).reach_notice()
    assert said and not any("Workbench" in line for line in said)


@pytest.mark.parametrize(("switch", "expect"), [("", "127.0.0.1"), ("true", "0.0.0.0")])
def test_dev_sh_says_where_it_listens(switch: str, expect: str) -> None:
    """`dev.sh status` is a person's way of asking; it starts and stops nothing.

    This is the half that cannot be checked from Python: the script builds the uvicorn command line,
    and a hard-coded 127.0.0.1 there would leave the setting above true and the socket local.
    """
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())}
    if switch:
        env["NEUROCODE_LISTEN_ON_LAN"] = switch
    said = subprocess.run([str(REPO / "scripts" / "dev.sh"), "status"], capture_output=True,
                          text=True, env=env, timeout=30).stdout
    assert expect in said
    assert "--host" not in said                       # it reports, it does not leak the command line
    source = (REPO / "scripts" / "dev.sh").read_text(encoding="utf-8")
    assert '--host "$API_HOST"' in source, "uvicorn is being given a fixed address again"

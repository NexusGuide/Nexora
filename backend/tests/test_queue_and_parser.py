"""Queue semantics and config URI parsing."""

from __future__ import annotations

import base64
import json

from app.services.config_parser import (
    is_probably_config,
    parse_config_uri,
)
from app.workers.queue import InMemoryQueue, Job
from app.workers.worker import lock_key_for


# --- queue ------------------------------------------------------------------
async def test_enqueue_and_reserve():
    queue = InMemoryQueue()
    await queue.enqueue("sync_usage", {"subscription_id": "s-1"})

    jobs = await queue.reserve()
    assert len(jobs) == 1
    assert jobs[0].name == "sync_usage"
    assert jobs[0].payload["subscription_id"] == "s-1"
    assert jobs[0].attempt == 1


async def test_retry_increments_the_attempt():
    queue = InMemoryQueue()
    await queue.enqueue("create_panel_user", {"order_id": "o-1"})
    job = (await queue.reserve())[0]

    await queue.retry(job, "panel down")
    retried = (await queue.reserve())[0]

    assert retried.attempt == 2
    assert retried.job_id == job.job_id  # same logical job


async def test_a_job_is_dead_lettered_after_the_attempt_limit():
    """A permanently broken job must stop consuming the worker forever."""
    queue = InMemoryQueue(max_attempts=3)
    await queue.enqueue("create_panel_user", {"order_id": "o-1"})

    for _ in range(5):
        jobs = await queue.reserve(block_ms=0)
        if not jobs:
            break
        await queue.retry(jobs[0], "still broken")

    assert len(queue.dead) == 1
    assert queue.dead[0][0].attempt == 3
    assert queue.depth == 0


async def test_delayed_jobs_are_not_delivered_early():
    queue = InMemoryQueue()
    await queue.enqueue("sync_usage", {}, delay_seconds=3600)

    assert await queue.reserve(block_ms=0) == []
    assert await queue.promote_due_jobs() == 0


async def test_backoff_widens_with_each_attempt():
    """A panel that is down stays down; hammering it helps nobody."""
    delays = [
        Job(name="x", payload={}, attempt=n).next_delay_seconds() for n in range(1, 6)
    ]
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_lock_key_isolates_by_subject():
    """Two jobs on the same subscription must serialise; different ones not."""
    a = Job(name="refresh_configs", payload={"subscription_id": "s-1"})
    b = Job(name="sync_usage", payload={"subscription_id": "s-1"})
    c = Job(name="sync_usage", payload={"subscription_id": "s-2"})
    order = Job(name="create_panel_user", payload={"order_id": "o-1"})
    sweep = Job(name="expire_subscriptions", payload={})

    assert lock_key_for(a) == lock_key_for(b) == "subscription:s-1"
    assert lock_key_for(c) != lock_key_for(a)
    assert lock_key_for(order) == "order:o-1"
    assert lock_key_for(sweep) == "job:expire_subscriptions"


def test_job_survives_a_serialisation_round_trip():
    job = Job(name="sync_usage", payload={"subscription_id": "s-1", "n": 3})
    restored = Job.from_fields(job.to_fields(), delivery_id="1-0")

    assert restored.name == job.name
    assert restored.payload == job.payload
    assert restored.job_id == job.job_id
    assert restored.delivery_id == "1-0"


# --- config parsing ---------------------------------------------------------
def test_vless_uri_is_parsed():
    parsed = parse_config_uri(
        "vless://uuid@de1.example.com:443?type=ws&security=tls#Germany%20Premium"
    )
    assert parsed.name == "Germany Premium"
    assert parsed.host == "de1.example.com"
    assert parsed.port == 443
    assert parsed.protocol == "vless/ws"


def test_vmess_base64_payload_is_parsed():
    payload = {"ps": "NL Node", "add": "nl1.example.com", "port": "8443"}
    uri = "vmess://" + base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).decode().rstrip("=")

    parsed = parse_config_uri(uri)
    assert parsed.name == "NL Node"
    assert parsed.host == "nl1.example.com"
    assert parsed.port == 8443
    assert parsed.protocol == "vmess"


def test_trojan_uri_is_parsed():
    parsed = parse_config_uri("trojan://pass@fi1.example.com:443#Finland")
    assert parsed.name == "Finland"
    assert parsed.host == "fi1.example.com"
    assert parsed.port == 443


def test_uri_without_a_fragment_falls_back_to_the_host():
    parsed = parse_config_uri("vless://uuid@de1.example.com:443?type=tcp")
    assert parsed.name == "de1.example.com"


def test_malformed_input_never_raises():
    """Panel output is untrusted data; a bad config must degrade, not crash."""
    for bad in [
        "",
        "   ",
        "not-a-uri",
        "vmess://!!!not-base64!!!",
        "vmess://" + base64.urlsafe_b64encode(b"{not json").decode(),
        "vless://uuid@host:notaport#x",
        "vless://",
        "://missing-scheme",
    ]:
        parsed = parse_config_uri(bad)
        assert isinstance(parsed.name, str)
        assert parsed.name  # never empty


def test_is_probably_config_rejects_noise():
    assert is_probably_config("vless://uuid@h.example.com:443#a")
    assert is_probably_config("vmess://abcdefghijklmnop")

    assert not is_probably_config("")
    assert not is_probably_config("# a comment from the panel")
    assert not is_probably_config("https://panel.example.com/sub/abc")
    assert not is_probably_config("vless://")


# --- subscription decoding ----------------------------------------------------
import base64 as _b64  # noqa: E402

from app.services.config_parser import decode_subscription  # noqa: E402

_LINKS = [
    "vless://u1@de.example.com:443?type=ws#DE",
    "trojan://p@nl.example.com:443#NL",
]


def test_decodes_a_base64_subscription():
    blob = _b64.b64encode("\n".join(_LINKS).encode()).decode()
    assert decode_subscription(blob) == _LINKS


def test_decodes_base64_that_is_wrapped_and_unpadded():
    blob = _b64.b64encode("\n".join(_LINKS).encode()).decode().rstrip("=")
    wrapped = "\n".join(blob[i : i + 20] for i in range(0, len(blob), 20))
    assert decode_subscription(wrapped) == _LINKS


def test_accepts_a_plain_text_subscription_and_drops_noise():
    body = "\n".join(["# comment", "", _LINKS[0], "not a uri", _LINKS[1], _LINKS[0]])
    assert decode_subscription(body) == _LINKS


def test_an_html_error_page_yields_nothing():
    assert decode_subscription("<html><body>502 Bad Gateway</body></html>") == []
    assert decode_subscription("") == []

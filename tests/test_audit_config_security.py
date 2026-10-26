"""Audit 2026-10-23 — P1-10 (CSRF/Host gate) and P2-16/P2-20/P2-25 (config).

New file: the modules under test (`main.py`, `core/config.py`,
`routes/manual_merger.py`) are edited in this wave, but the existing tests for
them are owned elsewhere, so the new behaviour is pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "config.example.yaml"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _example_dict() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def _write_cfg(tmp_path: Path, mutate=None) -> Path:
    data = _example_dict()
    if mutate is not None:
        mutate(data)
    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    return target


def _load(cfg_path: Path):
    from app.core.config import load_config

    return load_config(cfg_path)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# P1-10 — Origin/Referer guard
# ---------------------------------------------------------------------------


def test_cross_origin_post_is_refused(client):
    response = client.post("/no-such-route", headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
    assert "cross-origin" in response.json()["detail"].lower()


def test_same_origin_post_passes_the_guard(client):
    # Past the guard the missing route answers 404 — not 403.
    response = client.post("/no-such-route", headers={"Origin": "http://testserver"})

    assert response.status_code == 404


def test_post_without_origin_or_referer_is_allowed(client):
    """curl and programmatic HTMX clients send neither header."""
    assert client.post("/no-such-route").status_code == 404


def test_referer_is_checked_when_origin_is_absent(client):
    refused = client.post("/no-such-route", headers={"Referer": "http://evil.example/page"})
    allowed = client.post("/no-such-route", headers={"Referer": "http://testserver/dashboard"})

    assert refused.status_code == 403
    assert allowed.status_code == 404


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_every_state_changing_method_is_guarded(client, method):
    response = getattr(client, method)("/no-such-route", headers={"Origin": "http://evil.example"})

    assert response.status_code == 403


def test_reads_are_not_blocked_by_an_origin(client):
    response = client.get("/health", headers={"Origin": "http://evil.example"})

    assert response.status_code == 200


def test_opaque_null_origin_is_refused(client):
    """A sandboxed iframe sends `Origin: null`; it cannot match any host."""
    response = client.post("/no-such-route", headers={"Origin": "null"})

    assert response.status_code == 403


# H1-1: the guard compares the port as well as the host. Another web service on the same PC
# (a different port) must not be able to post to this one.


def test_same_host_different_port_is_refused(client):
    response = client.post(
        "/no-such-route", headers={"Host": "pc:8000", "Origin": "http://pc:9000"}
    )

    assert response.status_code == 403


def test_same_host_and_port_passes_the_guard(client):
    response = client.post(
        "/no-such-route", headers={"Host": "pc:8000", "Origin": "http://pc:8000"}
    )

    assert response.status_code == 404


def test_origin_default_port_equals_an_explicit_default_port_on_the_host(client):
    # Origin without a port is port 80; a Host that spells :80 is the same origin.
    assert (
        client.post(
            "/no-such-route", headers={"Host": "testserver:80", "Origin": "http://testserver"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/no-such-route", headers={"Host": "testserver", "Origin": "http://testserver:80"}
        ).status_code
        == 404
    )


def test_a_non_default_port_does_not_match_the_default_port(client):
    response = client.post(
        "/no-such-route", headers={"Host": "testserver", "Origin": "http://testserver:8000"}
    )

    assert response.status_code == 403


def test_https_origin_behind_a_proxy_that_forwards_the_bare_host_passes(client):
    # The scheme is not compared. Origin https://pc (443) against Host pc: the default port of
    # the ORIGIN's scheme is used on both sides, so a TLS-terminating proxy does not break it.
    response = client.post("/no-such-route", headers={"Host": "pc", "Origin": "https://pc"})

    assert response.status_code == 404


@pytest.mark.parametrize("origin", ["http://testserver:abc", "http://testserver:99999"])
def test_a_malformed_origin_port_is_refused_not_a_server_error(client, origin):
    response = client.post("/no-such-route", headers={"Origin": origin})

    assert response.status_code == 403


def test_referer_port_is_checked_too(client):
    refused = client.post(
        "/no-such-route", headers={"Host": "pc:8000", "Referer": "http://pc:9000/dashboard"}
    )
    allowed = client.post(
        "/no-such-route", headers={"Host": "pc:8000", "Referer": "http://pc:8000/dashboard"}
    )

    assert refused.status_code == 403
    assert allowed.status_code == 404


def test_an_ipv6_literal_host_and_port_match(client):
    response = client.post(
        "/no-such-route", headers={"Host": "[::1]:8000", "Origin": "http://[::1]:8000"}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# P1-10 — TrustedHost only when server.allowed_hosts is configured
# ---------------------------------------------------------------------------


def _middleware_names(app) -> list[str]:
    return [middleware.cls.__name__ for middleware in app.user_middleware]


def test_example_config_ships_the_open_host_default():
    cfg = _load(EXAMPLE)

    assert cfg.server.allowed_hosts == ["*"]


def test_trusted_host_is_not_mounted_by_default():
    from app.main import create_app

    app = create_app()

    assert "TrustedHostMiddleware" not in _middleware_names(app)


def test_trusted_host_is_mounted_when_configured():
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(allowed_hosts=["aam.test"])
    assert "TrustedHostMiddleware" in _middleware_names(app)

    with TestClient(app, base_url="http://aam.test") as allowed:
        assert allowed.post("/no-such-route").status_code == 404
    with TestClient(app, base_url="http://evil.test") as refused:
        assert refused.post("/no-such-route").status_code == 400


# ---------------------------------------------------------------------------
# P2-16 — a typo stops startup instead of silently running on the default
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_fails_at_startup(tmp_path):
    cfg_path = _write_cfg(tmp_path, lambda data: data.update({"pathz": {}}))

    with pytest.raises(Exception, match="pathz"):
        _load(cfg_path)


def test_unknown_nested_key_fails_at_startup(tmp_path):
    def mutate(data):
        data["ingestion"]["stability_pol_count"] = 2

    with pytest.raises(Exception, match="stability_pol_count"):
        _load(_write_cfg(tmp_path, mutate))


def test_dotenv_secrets_are_not_mistaken_for_config_typos(tmp_path, monkeypatch):
    """`.env` holds secrets, not config fields; `extra="forbid"` must not see them."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg_path = _write_cfg(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=sk-test-not-a-config-key\nUNRELATED=1\n", encoding="utf-8"
    )

    cfg = _load(cfg_path)

    assert cfg.vlm.api_key_env_var == "OPENROUTER_API_KEY"


def test_omitted_combined_folder_resolves_against_the_config_file(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    cfg_path = _write_cfg(tmp_path, lambda data: data["paths"].pop("combined_folder"))

    cfg = _load(cfg_path)

    assert Path(cfg.paths.combined_folder) == (tmp_path.resolve() / "data" / "combined")


# ---------------------------------------------------------------------------
# P2-20 — timing fields must be positive and internally consistent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("ingestion", "stability_poll_count", 0),
        ("ingestion", "stability_poll_interval_seconds", 0),
        ("vlm", "request_timeout_seconds", 0),
    ],
)
def test_non_positive_timing_fields_are_refused(tmp_path, section, key, value):
    def mutate(data):
        data[section][key] = value

    with pytest.raises(Exception, match=key):
        _load(_write_cfg(tmp_path, mutate))


def test_stale_processing_must_exceed_the_worst_case_attempt(tmp_path):
    """timeout * (retries + 1) + backoff is the floor, not a suggestion."""

    def mutate(data):
        data["vlm"]["request_timeout_seconds"] = 60
        data["extraction"]["max_retries"] = 3
        data["extraction"]["retry_backoff_seconds"] = [2, 5, 15]
        # Worst case: 60 * 4 + 22 = 262 seconds.
        data["extraction"]["stale_processing_seconds"] = 262

    with pytest.raises(Exception, match="stale_processing_seconds"):
        _load(_write_cfg(tmp_path, mutate))


def test_stale_processing_above_the_worst_case_is_accepted(tmp_path):
    def mutate(data):
        data["vlm"]["request_timeout_seconds"] = 60
        data["extraction"]["retry_backoff_seconds"] = [2, 5, 15]
        data["extraction"]["stale_processing_seconds"] = 263

    cfg = _load(_write_cfg(tmp_path, mutate))

    assert cfg.extraction.stale_processing_seconds == 263


# ---------------------------------------------------------------------------
# P2-25 — max_retries is bounded and its meaning is pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [0, 6])
def test_max_retries_outside_the_supported_range_is_refused(tmp_path, bad_value):
    def mutate(data):
        data["extraction"]["max_retries"] = bad_value

    with pytest.raises(Exception, match="max_retries"):
        _load(_write_cfg(tmp_path, mutate))


def test_example_max_retries_matches_the_engine_cap():
    """`extraction._attempt_cap` reads this value as the attempt count."""
    from app.core.config import ExtractionConfig
    from app.services.extraction import _attempt_cap

    cfg = _load(EXAMPLE)

    assert cfg.extraction.max_retries == 3
    assert _attempt_cap(cfg) == cfg.extraction.max_retries
    assert ExtractionConfig().max_retries == 3


# ---------------------------------------------------------------------------
# P2-15 — manual merger caps (the filename DOM safety is in test_audit_frontend)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manual_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _pdf_upload(name: str, payload: bytes = b"%PDF-1.4\n"):
    return ("files", (name, payload, "application/pdf"))


def test_manual_merge_refuses_more_than_the_file_cap(manual_client):
    from app.core.limits import MAX_MANUAL_MERGE_FILES

    uploads = [_pdf_upload(f"page-{index}.pdf") for index in range(MAX_MANUAL_MERGE_FILES + 1)]

    response = manual_client.post("/manual/merge", files=uploads)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Too many files" in detail
    assert str(MAX_MANUAL_MERGE_FILES) in detail


def test_manual_merge_refuses_more_than_the_total_byte_cap(manual_client, monkeypatch):
    from app.core.config import load_config

    cfg = load_config("config.example.yaml")
    cfg.manual_merge.max_total_mb = 1
    monkeypatch.setattr("app.api.routes.manual_merger.load_config", lambda: cfg)
    uploads = [
        _pdf_upload("a.pdf", b"%PDF-1.4" + b"a" * 600_000),
        _pdf_upload("b.pdf", b"%PDF-1.4" + b"b" * 600_000),
    ]

    response = manual_client.post("/manual/merge", files=uploads)

    # 413 is the correct semantic for an oversized payload (it used to be 422).
    assert response.status_code == 413
    assert "more than" in response.json()["detail"]


def test_load_config_is_cached_but_returns_independent_copies(tmp_path):
    cfg_path = _write_cfg(tmp_path)
    first = _load(cfg_path)
    second = _load(cfg_path)
    assert first is not second
    first.paths.input_folder = tmp_path / "mutated"
    third = _load(cfg_path)
    assert Path(third.paths.input_folder) != tmp_path / "mutated"

    data = _example_dict()
    data["paths"]["output_folder"] = str(tmp_path / "new-output")
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    reloaded = _load(cfg_path)
    assert Path(reloaded.paths.output_folder) == tmp_path / "new-output"

"""El formulario publico no debe servir para enviar emails a terceros."""
import re

from app import antispam, config
from tests.conftest import post_lead
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401  (fixtures compartidas)


def test_lead_requires_page_token(client, funnel_slug):
    slug = funnel_slug["slug"]
    r = client.post("/api/lead", json={"slug": slug, "email": "bot@ej.com"})
    assert r.status_code == 400
    r = client.post("/api/lead", json={"slug": slug, "email": "bot@ej.com", "la_t": "123.otro.firmafalsa"})
    assert r.status_code == 400


def test_token_from_another_funnel_is_rejected(client, funnel_slug):
    html = client.get("/p/vieja-abc123?preview=1").text
    token = re.search(r'window.__la_t="([^"]+)"', html).group(1)
    r = client.post("/api/lead", json={"slug": funnel_slug["slug"], "email": "x@ej.com", "la_t": token})
    assert r.status_code == 400


def test_invalid_email_and_unknown_funnel(client, funnel_slug):
    assert post_lead(client, funnel_slug["slug"], email="no-es-un-email").status_code == 422
    assert client.post("/api/lead", json={"slug": "no-existe", "email": "a@ej.com"}).status_code == 404


def test_too_fast_is_a_bot(client, funnel_slug, monkeypatch):
    monkeypatch.setattr(antispam, "LEAD_MIN_SECONDS", 60)
    assert post_lead(client, funnel_slug["slug"], email="rapido@ej.com").status_code == 400


def test_rate_limit_per_ip(client, funnel_slug, monkeypatch):
    monkeypatch.setattr(antispam, "LEAD_RATE_PER_IP", 2)
    monkeypatch.setattr(antispam, "limiter", antispam.RateLimiter())
    codes = [post_lead(client, funnel_slug["slug"], email=f"rl{i}@ej.com").status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_duplicate_email_does_not_restart_sequence(client, funnel_slug):
    slug = funnel_slug["slug"]
    first = post_lead(client, slug, email="Dup@Ej.com").json()
    second = post_lead(client, slug, email="dup@ej.com").json()
    assert not first.get("duplicate") and second["duplicate"] is True
    assert config.LEAD_MIN_SECONDS == 0  # el entorno de pruebas no espera

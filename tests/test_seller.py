"""Agente vendedor: chat en la landing y el mismo cerebro para ManyChat/FunnelChat."""
import pytest

from app import antispam, seller
from tests.test_emails import KEY
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401


@pytest.fixture
def fake_seller(monkeypatch):
    seen = {}

    async def fake(prompt, system, max_tokens=400):
        seen["prompt"], seen["system"] = prompt, system
        return {"respuesta": "Cuesta 147 USD y tienes 7 dias de garantia.", "mostrar_boton": True}
    monkeypatch.setattr(seller, "call_llm_json", fake)
    return seen


def test_widget_is_injected_with_landing_colors(client, funnel_slug):
    html = client.get(f"/p/{funnel_slug['slug']}?preview=1").text
    assert 'id="la-chat"' in html and "/api/seller/" in html and "#0f766e" in html  # acento de la landing
    assert '"preview": true' in html  # en la vista previa del panel no cuenta como conversacion real


def test_seller_answers_from_the_funnel_data(client, funnel_slug, fake_seller):
    slug = funnel_slug["slug"]
    r = client.post(f"/api/seller/{slug}", json={"messages": [{"role": "user", "content": "¿Cuanto cuesta?"}]})
    assert r.status_code == 200 and r.json() == {"respuesta": "Cuesta 147 USD y tienes 7 dias de garantia.", "mostrar_boton": True}
    assert "FICHA" in fake_seller["prompt"] and "objeciones_tipicas" in fake_seller["prompt"] and "¿Cuanto cuesta?" in fake_seller["prompt"]
    assert "Nunca inventes" in fake_seller["system"]
    events = client.get(f"/api/landings/{slug}/live", params={"since": 1}).json()["events"]
    assert "chat_message" in [e["event"] for e in events]


def test_seller_validation_limits_and_fallback(client, funnel_slug, monkeypatch):
    slug = funnel_slug["slug"]
    bad = {"messages": [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hola"}]}
    assert client.post(f"/api/seller/{slug}", json=bad).status_code == 400
    assert client.post(f"/api/seller/{slug}", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422

    async def broken(*a, **kw):
        raise RuntimeError("API caida")
    monkeypatch.setattr(seller, "call_llm_json", broken)
    out = client.post(f"/api/seller/{slug}", json={"messages": [{"role": "user", "content": "hola"}]}).json()
    assert out == seller.FALLBACK  # el visitante nunca ve un error tecnico

    monkeypatch.setattr(antispam, "limiter", antispam.RateLimiter())
    codes = [client.post(f"/api/seller/{slug}", json={"messages": [{"role": "user", "content": "hola"}]}).status_code
             for _ in range(32)]
    assert codes.count(429) >= 2  # 30 mensajes por IP cada 10 minutos


def test_manychat_external_request(client, funnel_slug, fake_seller):
    slug = funnel_slug["slug"]
    body = {"text": "precio?", "mc_id": "123456"}
    assert client.post(f"/api/manychat/{slug}/reply", json=body).status_code == 401
    r = client.post(f"/api/manychat/{slug}/reply", json=body, headers=KEY).json()
    msg = r["content"]["messages"][0]
    assert r["version"] == "v2" and msg["text"].startswith("Cuesta 147")
    assert msg["buttons"][0]["url"].endswith(f"/p/{slug}?utm_source=manychat&mc_id=123456")


def test_chat_starts_closed_and_can_be_disabled(client, funnel_slug, fake_seller):
    slug = funnel_slug["slug"]
    html = client.get(f"/p/{slug}?preview=1").text
    assert "#la-chat [hidden]{display:none!important}" in html and "box.hidden=true" in html  # cerrado hasta que lo abran

    assert client.put(f"/api/landings/{slug}/seller", json={"active": False}).json()["active"] is False
    assert client.get(f"/api/landings/{slug}").json()["seller_chat"] is False
    assert 'id="la-chat"' not in client.get(f"/p/{slug}?preview=1").text
    assert client.post(f"/api/seller/{slug}", json={"messages": [{"role": "user", "content": "hola"}]}).status_code == 403
    client.put(f"/api/landings/{slug}/seller", json={"active": True})
    assert 'id="la-chat"' in client.get(f"/p/{slug}?preview=1").text

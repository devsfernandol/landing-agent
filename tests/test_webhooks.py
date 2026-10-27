"""Webhooks salientes para el CRM: eventos, formato de contacto, firma, reintentos y registro."""
import hashlib
import hmac
import json

import pytest

from app import webhooks
from tests.conftest import pay, post_lead
from tests.test_payments import HOTTOK, hotmart, hotmart_mode  # noqa: F401
from tests.test_emails import sent  # noqa: F401
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401


@pytest.fixture
def receiver(monkeypatch):
    """Un CRM falso: guarda lo recibido y responde con los codigos que se le indiquen."""
    got, codes = [], []

    class Resp:
        def __init__(self, code):
            self.status_code, self.text = code, "error del CRM" if code >= 400 else "ok"

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            got.append({"url": url, "body": json.loads(content), "raw": content, "headers": headers})
            return Resp(codes.pop(0) if codes else 200)
    monkeypatch.setattr(webhooks.httpx, "AsyncClient", Client)
    monkeypatch.setattr(webhooks, "RETRIES", (0, 0, 0))
    yield got, codes
    webhooks.db.set_setting("webhooks", [])  # que ninguna otra prueba envie a un CRM


def configure(client, events=None, headers=None):
    hook = {"name": "n8n CRM", "url": "https://n8n.test/webhook/crm", "events": events or list(webhooks.EVENTS),
            "headers": headers or {"X-Automation-Key": "clave-n8n"}}
    r = client.put("/api/webhooks", json={"hooks": [hook]})
    assert r.status_code == 200
    return r.json()["hooks"][0]


def test_lead_and_purchase_reach_the_crm_signed(client, funnel_slug, receiver, sent):
    got, _ = receiver
    hook = configure(client)
    assert hook["headers"] == {"X-Automation-Key": "••••"} and len(hook["secret"]) == 48  # la clave no se devuelve
    slug = funnel_slug["slug"]
    client.cookies.clear()
    post_lead(client, slug, name="Ana Lopez", email="ana.crm@ej.es", utm_source="instagram")
    lead = got[-1]
    assert lead["body"]["event"] == "lead.created" and lead["body"]["stage"] == "Lead"
    assert lead["body"]["contact"] == {"email": "ana.crm@ej.es", "name": "Ana Lopez", "first_name": "Ana", "last_name": "Lopez"}
    assert lead["body"]["utm"]["utm_source"] == "instagram" and lead["body"]["landing"]["url"].endswith(f"/p/{slug}")
    assert lead["headers"]["X-Automation-Key"] == "clave-n8n"
    t, v1 = (kv.split("=", 1)[1] for kv in lead["headers"]["X-Landing-Signature"].split(","))
    assert v1 == hmac.new(hook["secret"].encode(), f"{t}.".encode() + lead["raw"], hashlib.sha256).hexdigest()

    pay(client, slug)
    buy = got[-1]["body"]
    assert buy["event"] == "purchase.completed" and buy["stage"] == "Cliente"
    assert buy["product"] == {"item": "main", "name": buy["product"]["name"], "price": 147, "currency": "USD"}

    log = client.get("/api/webhooks").json()["deliveries"]
    assert [d["event"] for d in log[:2]] == ["purchase.completed", "lead.created"] and all(d["ok"] for d in log[:2])


def test_retries_and_event_filter(client, funnel_slug, receiver, sent):
    got, codes = receiver
    configure(client, events=["purchase.completed"])
    slug = funnel_slug["slug"]
    client.cookies.clear()
    post_lead(client, slug, name="Sin CRM", email="nocrm@ej.es")
    assert got == []  # el webhook no esta suscrito a los leads

    codes.extend([500, 503])  # el CRM falla dos veces y a la tercera responde
    pay(client, slug)
    assert len(got) == 3
    d = client.get("/api/webhooks").json()["deliveries"][0]
    assert d["ok"] == 1 and d["attempts"] == 3 and d["status"] == 200


def test_client_error_is_not_retried_and_logged(client, funnel_slug, receiver):
    got, codes = receiver
    hook = configure(client)
    codes.append(401)
    r = client.post(f"/api/webhooks/{hook['id']}/test").json()
    assert r == {"ok": False, "status": 401, "error": "error del CRM"} and len(got) == 1  # una clave mala no se reintenta
    assert got[0]["body"]["test"] is True


def test_editing_keeps_secret_and_hidden_headers(client, receiver):
    hook = configure(client)
    again = client.put("/api/webhooks", json={"hooks": [{**hook, "name": "CRM"}]}).json()["hooks"][0]
    assert again["secret"] == hook["secret"] and again["name"] == "CRM"
    assert webhooks.hooks()[0]["headers"] == {"X-Automation-Key": "clave-n8n"}  # "••••" no pisa la clave real
    assert client.put("/api/webhooks", json={"hooks": [{"url": "ftp://x"}]}).status_code == 422
    client.put("/api/webhooks", json={"hooks": []})


def test_hotmart_refund_and_abandoned_cart_go_to_crm(client, hotmart_mode, receiver, sent):
    from app import payments
    got, _ = receiver
    configure(client, events=["purchase.refunded", "cart.abandoned"])
    slug = hotmart_mode
    client.cookies.clear()
    post_lead(client, slug, name="Rosa", email="rosa@ej.es")
    sck = payments.client_ref(sent[-1]["lead_id"], slug, "main", "hotmart")
    hotmart(client, "PURCHASE_APPROVED", sck)
    hotmart(client, "PURCHASE_REFUNDED", sck)
    assert got[-1]["body"]["event"] == "purchase.refunded" and got[-1]["body"]["reason"] == "Reembolso"
    r = hotmart(client, "PURCHASE_OUT_OF_SHOPPING_CART", slug=slug, email="carrito@ej.es")
    assert "enviado al CRM" in r.json()["result"]
    assert got[-1]["body"]["event"] == "cart.abandoned" and got[-1]["body"]["contact"]["email"] == "carrito@ej.es"
    client.put("/api/webhooks", json={"hooks": []})


def test_connect_my_n8n_in_one_click(client, receiver, monkeypatch):
    from app import main
    assert client.post("/api/webhooks/n8n").status_code == 400  # sin n8n configurado
    monkeypatch.setattr(main, "N8N_WEBHOOK_URL", "https://n8n.test/webhook/landing-agent-embudo")
    r = client.post("/api/webhooks/n8n").json()
    assert r["url"] == "https://n8n.test/webhook/landing-agent-crm"
    hooks = webhooks.hooks()
    assert len(hooks) == 1 and hooks[0]["headers"] == {"X-Automation-Key": "test-key"} and set(hooks[0]["events"]) == set(webhooks.EVENTS)
    client.post("/api/webhooks/n8n")
    assert len(webhooks.hooks()) == 1  # pulsarlo dos veces no duplica

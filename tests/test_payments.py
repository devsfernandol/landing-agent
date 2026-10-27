"""Pagos verificados con Stripe, IndexNow y llms.txt."""
import hashlib
import hmac
import json
import time

import pytest

from app import main, payments
from tests.conftest import post_lead
from tests.test_emails import KEY, mail, sent  # noqa: F401  (fixture compartida)
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401

SECRET = "whsec_test"


def stripe_post(client, event: dict, secret: str = SECRET, t: int | None = None):
    body = json.dumps(event).encode()
    t = t or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/stripe", content=body, headers={"Stripe-Signature": f"t={t},v1={sig}"})


def session(ref: str, status: str = "paid") -> dict:
    return {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": ref, "payment_status": status}}}


@pytest.fixture
def stripe_mode(client, funnel_slug, monkeypatch):
    monkeypatch.setattr(main, "STRIPE_WEBHOOK_SECRET", SECRET)
    slug = funnel_slug["slug"]
    client.put(f"/api/landings/{slug}/checkout", json={"main": "https://buy.stripe.com/test_123"})
    yield slug
    client.put(f"/api/landings/{slug}/checkout", json={})


def test_stripe_link_carries_signed_lead_reference(client, stripe_mode, sent):
    slug = stripe_mode
    client.cookies.clear()
    post_lead(client, slug, name="Rita", email="rita@ej.com")
    loc = client.get(f"/go/{slug}/checkout", follow_redirects=False).headers["location"]
    assert loc.startswith("https://buy.stripe.com/test_123?client_reference_id=") and "prefilled_email=rita%40ej.com" in loc
    ref = loc.split("client_reference_id=")[1].split("&")[0]
    assert payments.parse_ref(ref)[1:] == (slug, "main")
    assert payments.parse_ref(ref.replace("_main_", "_upsell_")) is None  # cambiar el producto rompe la firma


def test_only_stripe_confirms_the_purchase(client, stripe_mode, sent):
    slug = stripe_mode
    client.cookies.clear()
    post_lead(client, slug, name="Rita", email="rita2@ej.com")
    lead_id = sent[-1]["lead_id"]
    ref = payments.client_ref(lead_id, slug, "main")

    # la URL de exito es publica: visitarla no es una compra
    assert client.get(f"/go/{slug}/paid-main", follow_redirects=False).headers["location"] == f"/p/{slug}/upsell"
    assert not mail(client, lead_id, "access", item="main")["send"]

    assert stripe_post(client, session(ref), secret="otro").status_code == 400  # firma falsa
    assert stripe_post(client, session(ref), t=int(time.time()) - 3600).status_code == 400  # reenvio antiguo
    assert stripe_post(client, session(ref, status="unpaid")).json().get("ignored")
    assert stripe_post(client, session("1_x_main_abc")).json().get("ignored")

    r = stripe_post(client, session(ref))
    assert r.status_code == 200 and r.json()["purchase"] == "main"
    assert sent[-1]["type"] == "purchase_main" and sent[-1]["lead_id"] == lead_id
    assert mail(client, lead_id, "access", item="main")["send"]
    n = len(sent)
    stripe_post(client, session(ref))  # Stripe reintenta: no duplica
    assert len(sent) == n


def test_webhook_disabled_without_secret(client):
    assert client.post("/api/webhooks/stripe", content=b"{}").status_code == 404


def test_llms_txt_and_indexnow_key(client, funnel_slug):
    txt = client.get("/llms.txt").text
    assert txt.startswith("# Embudos publicados") and f"/p/{funnel_slug['slug']})" in txt
    key = client.get("/indexnow.txt").text
    assert len(key) == 32 and key == client.get("/indexnow.txt").text


# ---------- Hotmart ----------
HOTTOK = "hottok-test"


@pytest.fixture
def hotmart_mode(client, funnel_slug, monkeypatch):
    monkeypatch.setattr(main, "HOTMART_HOTTOK", HOTTOK)
    slug = funnel_slug["slug"]
    client.put(f"/api/landings/{slug}/checkout", json={"main": "https://pay.hotmart.com/X123ABC"})
    yield slug
    client.put(f"/api/landings/{slug}/checkout", json={})


def hotmart(client, event, sck="", token=HOTTOK, slug="", email="x@ej.com", product="Meditaciones", offer=""):
    body = {"id": "evt-1", "event": event, "version": "2.0.0",
            "data": {"product": {"id": 8808443, "name": product}, "buyer": {"email": email, "name": "Carmen"},
                     "purchase": {"status": "APPROVED", "transaction": "HP123", "price": {"value": 19.9, "currency_value": "EUR"},
                                  "offer": {"code": offer}, "origin": {"sck": sck} if sck else {}}}}
    url = "/api/webhooks/hotmart" + (f"/{slug}" if slug else "")
    return client.post(url, json=body, headers={"X-HOTMART-HOTTOK": token})


def test_hotmart_link_carries_sck_and_prefill(client, hotmart_mode, sent):
    slug = hotmart_mode
    client.cookies.clear()
    post_lead(client, slug, name="Carmen", email="carmen@ej.es")
    loc = client.get(f"/go/{slug}/checkout", follow_redirects=False).headers["location"]
    assert loc.startswith("https://pay.hotmart.com/X123ABC?sck=") and "email=carmen%40ej.es" in loc and "name=Carmen" in loc
    sck = loc.split("sck=")[1].split("&")[0]
    assert payments.parse_ref(sck, "hotmart")[1:] == (slug, "main")
    assert payments.parse_ref(sck, "stripe") is None  # una referencia de Hotmart no sirve para Stripe


def test_hotmart_purchase_and_refund(client, hotmart_mode, sent):
    slug = hotmart_mode
    client.cookies.clear()
    post_lead(client, slug, name="Carmen", email="carmen2@ej.es")
    lead_id = sent[-1]["lead_id"]
    sck = payments.client_ref(lead_id, slug, "main", "hotmart")

    assert client.get(f"/go/{slug}/paid-main", follow_redirects=False).headers["location"] == f"/p/{slug}/upsell"
    assert not mail(client, lead_id, "access", item="main")["send"]  # la URL de exito no es una compra
    assert hotmart(client, "PURCHASE_APPROVED", sck, token="falso").status_code == 401
    assert "Sin landing" in hotmart(client, "PURCHASE_APPROVED", "1_x_main_abc").json()["result"]
    assert "no requiere" in hotmart(client, "PURCHASE_BILLET_PRINTED", sck).json()["result"]  # aun no pago

    assert "Compra registrada" in hotmart(client, "PURCHASE_APPROVED", sck).json()["result"]
    assert sent[-1]["type"] == "purchase_main" and mail(client, lead_id, "access", item="main")["send"]
    assert "ya registrada" in hotmart(client, "PURCHASE_COMPLETE", sck).json()["result"]  # aprobada + completa: una venta
    revenue = client.get(f"/api/landings/{slug}").json()["metrics"]["revenue"]

    assert "Acceso cortado" in hotmart(client, "PURCHASE_REFUNDED", sck).json()["result"]
    assert not mail(client, lead_id, "access", item="main")["send"]  # reembolsado: sin email de acceso
    assert client.get(f"/api/landings/{slug}").json()["metrics"]["revenue"] < revenue  # ni ingreso


def test_landing_webhook_matches_buyer_by_email_and_logs_everything(client, hotmart_mode, sent):
    slug = hotmart_mode
    client.cookies.clear()
    post_lead(client, slug, name="Lucia", email="lucia@ej.es")
    lead_id = sent[-1]["lead_id"]
    assert hotmart(client, "PURCHASE_APPROVED", slug="no-existe").status_code == 404

    # sin sck (p. ej. compro desde el link de Hotmart directamente), pero con el webhook de la landing y su email
    r = hotmart(client, "PURCHASE_APPROVED", slug=slug, email="LUCIA@ej.es")
    assert "Compra registrada" in r.json()["result"] and sent[-1]["lead_id"] == lead_id

    # compra sin haber sido lead: se crea el lead para poder entregarle el producto
    r = hotmart(client, "PURCHASE_APPROVED", slug=slug, email="nuevo@ej.es")
    assert "Compra registrada" in r.json()["result"] and sent[-1]["email"] == "nuevo@ej.es"

    # el boton "Prueba tu configuracion" de Hotmart: conexion correcta, sin tocar datos
    r = hotmart(client, "PURCHASE_APPROVED", slug=slug, email="testeComprador@example.com", product="Produto test postback2")
    assert "Prueba de Hotmart" in r.json()["result"]
    hotmart(client, "PURCHASE_OUT_OF_SHOPPING_CART", slug=slug, email="carrito@ej.es")

    log = client.get("/api/payments/events", params={"slug": slug}).json()
    ev = log["events"]
    assert [e["event"] for e in ev[:2]] == ["PURCHASE_OUT_OF_SHOPPING_CART", "PURCHASE_APPROVED"]
    assert ev[0]["label"] == "Abandono de carrito" and ev[1]["test"] == 1
    assert ev[2]["buyer"] == "nu***@ej.es" and ev[2]["amount"] == "19.9 EUR" and ev[2]["landing"]
    assert log["webhook_base"].endswith("/api/webhooks/hotmart")


def test_hotmart_disabled_without_hottok(client):
    assert client.post("/api/webhooks/hotmart", json={}).status_code == 404


def test_hotmart_test_events_are_flagged():
    assert payments.is_test({"event": "SWITCH_PLAN", "data": {"subscription": {"product": {"name": "Product name com ç e á"}}}})
    assert payments.is_test({"event": "PURCHASE_APPROVED", "data": {"buyer": {"email": "testeComprador271101postman15@example.com"}}})
    assert not payments.is_test({"event": "PURCHASE_APPROVED", "data": {"buyer": {"email": "carmen@ej.es"}, "product": {"name": "Meditaciones"}}})


def test_declined_payment_does_not_revoke_other_purchases(client, hotmart_mode, sent):
    """Caso real: el lead ya tenia una compra (checkout demo) y despues una tarjeta rechazada en Hotmart envia PURCHASE_CANCELED."""
    slug = hotmart_mode
    client.cookies.clear()
    post_lead(client, slug, name="Fer", email="fer@ej.es")
    lead_id = sent[-1]["lead_id"]
    sck = payments.client_ref(lead_id, slug, "main", "hotmart")
    assert "Compra registrada" in hotmart(client, "PURCHASE_APPROVED", sck).json()["result"]  # transaccion HP123

    def cancel(tx):
        body = {"event": "PURCHASE_CANCELED", "data": {"buyer": {"email": "fer@ej.es"}, "product": {"name": "Meditaciones"},
                                                       "purchase": {"transaction": tx, "origin": {"sck": sck}}}}
        return client.post(f"/api/webhooks/hotmart/{slug}", json=body, headers={"X-HOTMART-HOTTOK": HOTTOK}).json()["result"]
    assert "Pago no completado" in cancel("HP-TARJETA-RECHAZADA")
    assert mail(client, lead_id, "access", item="main")["send"]  # la compra buena sigue intacta
    assert "Acceso cortado" in cancel("HP123")  # cancelar la transaccion que si se aprobo, revierte
    cats = {e["event"]: e["category"] for e in client.get("/api/payments/events", params={"slug": slug}).json()["events"]}
    assert cats["PURCHASE_CANCELED"] == "reverso" and cats["PURCHASE_APPROVED"] == "compra"

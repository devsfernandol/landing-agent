"""Secuencias de email: calendario hacia n8n, decision de envio en la app, acceso firmado y baja."""
import pytest

from app import integrations
from tests.conftest import pay, post_lead
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401  (fixtures compartidas)

KEY = {"X-Automation-Key": "test-key"}


@pytest.fixture
def sent(monkeypatch):
    payloads = []

    async def fake_send(payload):
        payloads.append(payload)
        return True
    monkeypatch.setattr(integrations, "send_n8n", fake_send)
    return payloads


def new_lead(client, slug, email):
    client.cookies.clear()
    r = post_lead(client, slug, name="Luis", email=email)
    assert r.cookies.get(f"lead_{slug}"), "el lead debe quedar vinculado al navegador"
    return r


def mail(client, lead_id, kind, **kw):
    return client.get("/api/automation/email", params={"lead": lead_id, "kind": kind, **kw}, headers=KEY).json()


def test_recovery_sequence_stops_after_purchase(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "luis@ej.com")
    lead = sent[-1]
    assert lead["type"] == "lead" and lead["api_base"] == "https://demo.test"
    assert [x["kind"] for x in lead["schedule"]] == ["recovery"]  # 1 email en el LLM simulado
    assert lead["schedule"][0]["send_at"] > lead["ts"]  # el primero no sale mientras esta en el checkout

    m = mail(client, lead["lead_id"], "recovery", n=0)
    assert m["send"] and m["to"] == "luis@ej.com"
    assert f"/go/{slug}/checkout?lt=" in m["html"] and "/baja/" in m["html"]
    assert "#0f766e" in m["html"]  # el boton usa el color de acento de la landing

    pay(client, slug)
    buy = sent[-1]
    assert buy["type"] == "purchase_main" and [x["kind"] for x in buy["schedule"]] == ["access", "checkin"]
    stop = mail(client, lead["lead_id"], "recovery", n=0)
    assert stop["send"] is False and "ya compro" in stop["reason"]
    assert mail(client, lead["lead_id"], "access", item="main")["send"]
    assert not mail(client, lead["lead_id"], "access", item="upsell")["send"]  # no lo compro

    pay(client, slug)  # recargar la pagina de exito no duplica
    assert sent[-1] is buy


def test_access_link_is_signed_and_replaceable(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "eva@ej.com")
    lead_id = sent[-1]["lead_id"]
    pay(client, slug)
    html = mail(client, lead_id, "access", item="main")["html"]
    url = html.split('href="https://demo.test/acceso/')[1].split('"')[0]
    assert "Acceso de ejemplo" in client.get(f"/acceso/{url}").text
    assert client.get(f"/acceso/{url[:-1]}x").status_code == 404  # firma alterada
    other = url.replace(f"{lead_id}.", f"{lead_id + 1}.", 1)
    assert client.get(f"/acceso/{other}").status_code == 404

    client.put(f"/api/landings/{slug}/delivery", json={"main": "https://notion.so/curso-real"})
    assert client.get(f"/acceso/{url}", follow_redirects=False).headers["location"] == "https://notion.so/curso-real"
    assert "https://notion.so/curso-real" in mail(client, lead_id, "access", item="main")["html"]


def test_email_link_identifies_lead_on_another_device(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "sol@ej.com")
    lead_id = sent[-1]["lead_id"]
    link = mail(client, lead_id, "recovery", n=0)["html"].split(f'href="https://demo.test/go/{slug}/checkout?lt=')[1].split('"')[0]
    client.cookies.clear()  # otro dispositivo: sin cookie del lead
    client.get(f"/go/{slug}/checkout?lt={link}", follow_redirects=False)
    pay(client, slug)
    assert sent[-1]["lead_id"] == lead_id
    assert not mail(client, lead_id, "recovery", n=0)["send"]


def test_unsubscribe_and_auth(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "baja@ej.com")
    lead_id = sent[-1]["lead_id"]
    html = mail(client, lead_id, "recovery", n=0)["html"]
    token = html.split('href="https://demo.test/baja/')[1].split('"')[0]
    assert client.get(f"/baja/{token}").status_code == 200
    assert mail(client, lead_id, "recovery", n=0)["reason"] == "se dio de baja"
    assert client.get("/api/automation/email", params={"lead": lead_id, "kind": "recovery"}).status_code == 401


def test_email_preview_in_panel(client, funnel_slug):
    slug = funnel_slug["slug"]
    for q in ("kind=recovery&n=0", "kind=access&item=upsell", "kind=checkin"):
        r = client.get(f"/api/landings/{slug}/email-preview?{q}")
        assert r.status_code == 200 and "Darme de baja" in r.text


def test_declining_everything_is_not_a_purchase(client, funnel_slug, sent):
    """El cliente se registra, llega al checkout y lo rechaza todo: ni compra, ni email de acceso, ni "gracias por tu compra"."""
    slug = funnel_slug["slug"]
    new_lead(client, slug, "no@ej.com")
    lead_id = sent[-1]["lead_id"]
    n = len(sent)
    client.get(f"/go/{slug}/checkout", follow_redirects=False)
    assert client.get(f"/go/{slug}/cancel-main", follow_redirects=False).headers["location"] == f"/p/{slug}"
    # escribir la URL de exito a mano no registra una compra en el modo demostracion
    assert client.get(f"/go/{slug}/paid-main", follow_redirects=False).headers["location"] == f"/checkout/{slug}/main"
    assert client.get(f"/go/{slug}/paid-main?ck=falso", follow_redirects=False).headers["location"] == f"/checkout/{slug}/main"
    assert client.get(f"/go/{slug}/upsell-no", follow_redirects=False).status_code == 303
    assert client.get(f"/go/{slug}/cancel-downsell", follow_redirects=False).headers["location"] == f"/p/{slug}/gracias"

    assert len(sent) == n  # nada se envio a n8n como compra
    assert not mail(client, lead_id, "access", item="main")["send"]
    assert mail(client, lead_id, "recovery", n=0)["send"]  # la recuperacion sigue activa
    thanks = client.get(f"/p/{slug}/gracias").text
    assert "Tu pedido no se completo" in thanks and "Gracias por tu compra" not in thanks


def test_thanks_page_lists_what_was_bought(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "si@ej.com")
    pay(client, slug)
    thanks = client.get(f"/p/{slug}/gracias").text
    assert "Tu pedido" in thanks and "Pedido completo" in thanks and "no se completo" not in thanks

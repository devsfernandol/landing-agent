"""Meta Conversions API: Lead y Purchase desde el servidor, con el mismo event_id que el pixel y datos hasheados."""
import hashlib
import json

import pytest

from app import capi
from tests.conftest import pay, post_lead
from tests.test_emails import sent  # noqa: F401
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


@pytest.fixture
def meta(monkeypatch, client):
    got = []

    class Resp:
        status_code, text = 200, '{"events_received":1}'

        def json(self):
            return {"events_received": 1}

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, json=None):
            got.append({"url": url, "params": params, "body": json})
            return Resp()
    monkeypatch.setattr(capi.httpx, "AsyncClient", Client)
    r = client.put("/api/meta-capi", json={"pixel_id": "123456789", "token": "EAAsecreto", "test_code": "TEST42"})
    assert r.json()["token"] == "••••" and r.json()["enabled"] is True  # el token no se devuelve
    yield got
    capi.db.set_setting("meta_capi", {})


def test_lead_and_purchase_sent_server_side_with_shared_event_id(client, funnel_slug, meta, sent):
    slug = funnel_slug["slug"]
    client.cookies.clear()
    client.cookies.set("_fbp", "fb.1.1700000000.111")
    r = post_lead(client, slug, name="Ana Lopez", email="Ana@Ej.es", fbclid="IwAR-clic")
    event_id = r.json()["event_id"]
    lead = meta[-1]
    assert lead["url"].endswith("/123456789/events") and lead["params"] == {"access_token": "EAAsecreto"}
    ev = lead["body"]["data"][0]
    assert lead["body"]["test_event_code"] == "TEST42"
    assert ev["event_name"] == "Lead" and ev["event_id"] == event_id and ev["action_source"] == "website"
    ud = ev["user_data"]
    assert ud["em"] == [sha("ana@ej.es")] and ud["fn"] == [sha("ana")]  # hasheados, nunca en claro
    assert ud["fbp"] == "fb.1.1700000000.111" and ud["fbc"].endswith(".IwAR-clic") and ud["client_user_agent"]

    pay(client, slug)
    buy = meta[-1]["body"]["data"][0]
    assert buy["event_name"] == "Purchase" and buy["event_id"] == f"{event_id.replace('lead.', 'purchase.')}.main"
    assert buy["custom_data"]["value"] == 147 and buy["custom_data"]["currency"] == "USD"
    assert buy["user_data"]["fbp"] == "fb.1.1700000000.111"  # la compra reutiliza lo guardado con el lead

    upsell = client.get(f"/p/{slug}/upsell").text  # el pixel del navegador usa el mismo id: Meta deduplica
    assert f"eventID:\"{buy['event_id']}\"" in upsell
    log = client.get("/api/webhooks").json()["deliveries"]
    assert [d["event"] for d in log[:2]] == ["Purchase", "Lead"] and log[0]["email"] == "(hasheado)"


def test_capi_off_without_config_and_test_button(client, funnel_slug, meta):
    assert client.post("/api/meta-capi/test").json()["ok"] is True
    assert "test_event_code" in meta[-1]["body"]
    client.put("/api/meta-capi", json={"pixel_id": "123456789", "token": "••••", "active": False})
    assert capi.config()["token"] == "EAAsecreto"  # "••••" no pisa el token guardado
    n = len(meta)
    client.cookies.clear()
    post_lead(client, funnel_slug["slug"], name="Sin Meta", email="nometa@ej.es")
    assert len(meta) == n  # desactivada: no envia nada
    assert client.put("/api/meta-capi", json={"pixel_id": "abc"}).status_code == 422

"""Edicion manual desde el panel: editor visual de la landing (GrapesJS) y textos de los emails."""
from tests.test_emails import KEY, mail, new_lead, sent  # noqa: F401  (fixture compartida)
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401
from tests.conftest import pay


def test_editor_roundtrip_keeps_seo_form_and_scripts(client, funnel_slug):
    slug = funnel_slug["slug"]
    assert client.get(f"/editor/{slug}").status_code == 200
    src = client.get(f"/api/landings/{slug}/editor").json()
    assert "<script" not in src["html"] and "<h1" in src["html"]  # el editor no ve ni puede romper los scripts

    edited = src["html"].replace("<h1", '<h1 onclick="alert(1)"', 1) + '<section><h2>Bonus nuevo</h2><script>alert(1)</script></section>'
    r = client.put(f"/api/landings/{slug}/html", json={"html": edited, "css": "h2{color:#c00}"})
    assert r.status_code == 200 and r.json()["seo_score"] > 0
    page = client.get(f"/p/{slug}?preview=1").text
    assert "Bonus nuevo" in page and 'id="la-editor"' in page and "h2{color:#c00}" in page
    assert "alert(1)" not in page  # ni scripts ni manejadores inline desde el editor
    assert "/api/lead" in page and "application/ld+json" in page  # formulario y SEO intactos

    client.put(f"/api/landings/{slug}/html", json={"html": edited, "css": "h2{color:#00c}"})
    assert client.get(f"/p/{slug}?preview=1").text.count('id="la-editor"') == 1  # se reemplaza, no se acumula
    assert client.post(f"/api/undo/{slug}").status_code == 200  # la edicion se puede deshacer
    assert "h2{color:#c00}" in client.get(f"/p/{slug}?preview=1").text


def test_recovery_email_edit_applies_to_pending_sends(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "edit@ej.com")
    lead_id = sent[-1]["lead_id"]
    items = client.get(f"/api/landings/{slug}/emails").json()["emails"]
    assert [m["kind"] for m in items] == ["recovery", "access", "checkin"]

    draft = {"kind": "recovery", "n": 0, "asunto": "Ultima llamada", "cuerpo": "Linea uno\nLinea dos", "boton": "Lo quiero"}
    pv = client.post(f"/api/landings/{slug}/emails/preview", json=draft).json()
    assert pv["subject"] == "Ultima llamada" and "Lo quiero" in pv["html"]
    assert mail(client, lead_id, "recovery", n=0)["subject"] != "Ultima llamada"  # la vista previa no guarda

    assert client.put(f"/api/landings/{slug}/emails", json=draft).status_code == 200
    m = mail(client, lead_id, "recovery", n=0)  # lo que n8n recibe al enviar el email ya programado
    assert m["subject"] == "Ultima llamada" and "Linea dos" in m["html"] and "Lo quiero" in m["html"]
    assert client.put(f"/api/landings/{slug}/emails", json={**draft, "asunto": ""}).status_code == 400
    assert client.put(f"/api/landings/{slug}/emails", json={**draft, "n": 9}).status_code == 400


def test_purchase_email_template_with_placeholders(client, funnel_slug, sent):
    slug = funnel_slug["slug"]
    new_lead(client, slug, "tpl@ej.com")
    lead_id = sent[-1]["lead_id"]
    pay(client, slug)
    edit = {"kind": "access", "asunto": "{nombre}, ya tienes {producto}", "cuerpo": "Hola {nombre}\n<b>Gracias</b>"}
    assert client.put(f"/api/landings/{slug}/emails", json=edit).status_code == 200
    m = mail(client, lead_id, "access", item="main")
    assert m["subject"].startswith("Luis, ya tienes ") and "&lt;b&gt;Gracias" in m["html"]  # el texto se escapa
    client.put(f"/api/landings/{slug}/emails", json={"kind": "access"})  # vacio = vuelve a la plantilla original
    assert mail(client, lead_id, "access", item="main")["subject"].startswith("Tu acceso a ")

"""Recorrido completo con LLM simulado: agente -> autocorreccion -> embudo -> leads con UTM -> compras -> metricas."""
import time

import pytest
from fastapi.testclient import TestClient

from app import agent, main, optimizer
from tests import fake_llm
from tests.conftest import pay, post_lead


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(agent, "call_llm", fake_llm.call_llm)
    mp.setattr(agent, "call_llm_json", fake_llm.call_llm_json)
    with TestClient(main.app) as c:
        yield c
    mp.undo()


def wait_job(client, job_id: str, timeout: float = 10) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("el trabajo no termino")


@pytest.fixture(scope="module")
def funnel_slug(client):
    r = client.post("/api/generate", data={"prompt": "Curso online de Excel para contadores, 147 USD, garantia 14 dias."})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job"])
    assert job["status"] == "done", job
    return job


def test_agent_plans_executes_and_self_corrects(funnel_slug):
    kinds = [t["kind"] for t in funnel_slug["trace"]]
    assert kinds[0] == "think" and "decision" in kinds and kinds[-1] == "done"
    assert "handoff" in kinds, "la landing v1 tiene fallos: los revisores deben devolverla al Copywriter"
    assert {"landing", "assets", "funnel", "meta", "refine", "critic"} <= set(fake_llm.calls)
    agents_seen = {t["agent"] for t in funnel_slug["trace"] if t["agent"]}
    assert agents_seen == {"estratega", "copywriter", "arquitecto", "contenido", "auditor", "critico"}
    assert all(a["status"] == "listo" for a in funnel_slug["agents"])
    assert any("Manejo de objeciones (5)" in t["text"] for t in funnel_slug["trace"])  # el critico explica por que rechaza
    res = funnel_slug["result"]
    assert res["seo_score"] >= 90
    assert res["conv"] == "6/6"
    assert res["critic"] == 8.0  # el critico aprueba la version corregida


def test_published_page_has_seo_and_tracking(client, funnel_slug):
    slug = funnel_slug["slug"]
    html = client.get(f"/p/{slug}").text
    assert '<link href="https://demo.test/p/' in html and 'rel="canonical"' in html
    assert '"@type": "Course"' in html and '"@type": "FAQPage"' in html
    assert "la-consent" in html
    assert f"/go/{slug}/checkout" in html  # tras el lead, el embudo continua al checkout
    assert client.get("/sitemap.xml").text.count(slug) == 1


def test_full_funnel_journey_and_metrics(client, funnel_slug):
    slug = funnel_slug["slug"]
    client.cookies.clear()
    client.get(f"/p/{slug}?mc_id=987654")
    r = post_lead(client, slug, name="Ana", email="ana@ej.com", utm_source="manychat",
                  utm_campaign="comentario-excel", mc_id="987654")
    assert r.json()["ok"]

    def go(action):
        return client.get(f"/go/{slug}/{action}", follow_redirects=False)

    assert go("checkout").headers["location"] == f"/checkout/{slug}/main"  # sin link real: checkout demo
    assert client.get(f"/checkout/{slug}/main").status_code == 200
    assert pay(client, slug, "main").headers["location"] == f"/p/{slug}/upsell"
    upsell = client.get(f"/p/{slug}/upsell").text
    assert "Pack de plantillas contables" in upsell and "'Purchase'" in upsell
    assert go("upsell-no").headers["location"] == f"/p/{slug}/downsell"
    client.get(f"/p/{slug}/downsell")
    assert go("downsell-yes").headers["location"] == f"/checkout/{slug}/downsell"
    assert pay(client, slug, "downsell").headers["location"] == f"/p/{slug}/gracias"
    assert client.get(f"/p/{slug}/gracias").status_code == 200

    d = client.get(f"/api/landings/{slug}").json()
    steps = {s["label"]: s for s in d["metrics"]["steps"]}
    assert steps["Leads"]["n"] == 1 and steps["Compra principal"]["n"] == 1 and steps["Downsell comprado"]["n"] == 1
    assert d["metrics"]["revenue"] == 147 + 97 and d["metrics"]["aov"] == 244
    assert d["channels"] == {"manychat": 1}
    csv = client.get("/api/leads.csv").text
    assert "manychat" in csv and "comentario-excel" in csv


def test_real_checkout_links_are_used(client, funnel_slug):
    slug = funnel_slug["slug"]
    r = client.put(f"/api/landings/{slug}/checkout", json={"main": "https://buy.stripe.com/test_123"})
    assert r.json()["success_urls"]["main"].endswith(f"/go/{slug}/paid-main")
    client.cookies.clear()  # visitante anonimo: el link va tal cual
    assert client.get(f"/go/{slug}/checkout", follow_redirects=False).headers["location"] == "https://buy.stripe.com/test_123"
    assert client.put(f"/api/landings/{slug}/checkout", json={"main": "javascript:alert(1)"}).status_code == 400


def test_ab_split_and_attribution(client, funnel_slug):
    slug = funnel_slug["slug"]
    assert client.post(f"/api/landings/{slug}/ab", json={"active": True}).json()["active"]
    seen = set()
    for _ in range(30):
        client.cookies.clear()
        r = client.get(f"/p/{slug}")
        seen.add(r.cookies.get(f"ab_{slug}"))
    assert seen == {"A", "B"}
    client.cookies.clear()
    client.cookies.set(f"ab_{slug}", "B")
    b_html = client.get(f"/p/{slug}").text
    assert "Curso de Excel para contadores que automatiza tus reportes" in b_html
    post_lead(client, slug, email="b@ej.com")
    v = client.get(f"/api/landings/{slug}").json()["variants"]
    assert v["B"]["leads"] == 1


def test_legacy_data_imported(client):
    rows = {r["slug"]: r for r in client.get("/api/landings").json()}
    assert rows["vieja-abc123"]["views"] == 7 and rows["vieja-abc123"]["leads"] == 1
    assert not rows["vieja-abc123"]["has_funnel"]
    assert client.get("/p/vieja-abc123/upsell").status_code == 404


def test_report_renders(client, funnel_slug):
    html = client.get(f"/estrategia/{funnel_slug['slug']}").text
    for part in ("Embudo de venta", "Guiones de video", "Traza del equipo de agentes", "Auditoria de conversion",
                 "Origen de los leads", "Evaluacion del critico de conversion: 8.0/10 (la primera version obtuvo 6.5)"):
        assert part in html

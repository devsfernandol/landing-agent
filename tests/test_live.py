"""Demo en vivo (QR + actividad en tiempo real) y trafico inteligente (Thompson sampling)."""
import random

from app import funnel
from tests.conftest import post_lead
from tests.test_pipeline import client, funnel_slug, wait_job  # noqa: F401


def share_of_b(v, n=2000):
    rng = random.Random(1)
    return sum(funnel.bandit_choice(v, rng) == "B" for _ in range(n)) / n


def test_bandit_explores_then_exploits():
    cold = {"A": {"views": 10, "leads": 0}, "B": {"views": 10, "leads": 5}}
    assert 0.4 < share_of_b(cold) < 0.6  # menos de 50 visitas: reparto 50/50
    clear = {"A": {"views": 300, "leads": 9}, "B": {"views": 300, "leads": 36}}
    assert share_of_b(clear) > 0.95  # B convierte 4 veces mas: se lleva casi todo el trafico
    tie = {"A": {"views": 300, "leads": 15}, "B": {"views": 300, "leads": 15}}
    assert 0.35 < share_of_b(tie) < 0.65  # empate: sigue explorando las dos
    odd = {"A": {"views": 60, "leads": 0}, "B": {"views": 0, "leads": 7}}  # leads sin visitas medidas
    funnel.bandit_choice(odd)  # no rompe la distribucion beta
    s = funnel.ab_summary(clear)
    assert s["traffic_b"] > 95 and "B recibe" in s["routing"]


def test_qr_and_live_feed(client, funnel_slug):
    slug = funnel_slug["slug"]
    svg = client.get(f"/api/landings/{slug}/qr.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml") and b"<svg" in svg.content

    start = client.get(f"/api/landings/{slug}/live").json()
    assert start["url"].endswith(f"/p/{slug}?utm_source=qr&utm_medium=demo-en-vivo") and start["leads"] == []
    client.cookies.clear()
    client.get(f"/p/{slug}?utm_source=qr")
    post_lead(client, slug, name="Entrevistador", email="jefe@empresa.com", utm_source="qr")
    client.get(f"/go/{slug}/checkout", follow_redirects=False)
    live = client.get(f"/api/landings/{slug}/live", params={"since": start["now"]}).json()
    lead = live["leads"][-1]
    assert lead["name"] == "Entrevistador" and lead["email"] == "je***@empresa.com" and lead["utm_source"] == "qr"
    assert "checkout" in [e["event"] for e in live["events"]] and live["views"] >= start["views"] + 1

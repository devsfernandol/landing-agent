"""Entorno aislado: datos en un directorio temporal, sin clave del panel y sin integraciones externas.
Las variables se fijan antes de importar la app porque config.py las lee al importarse (y dotenv no las sobrescribe)."""
import json
import os
import sys
import tempfile
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="landing-agent-test-"))
os.environ.update({"DATA_DIR": str(DATA), "APP_PASSWORD": "", "N8N_WEBHOOK_URL": "", "MANYCHAT_API_KEY": "",
                   "PUBLIC_URL": "https://demo.test", "META_PIXEL_ID": "", "GA4_ID": "",
                   "AUTOMATION_KEY": "test-key", "HOTMART_HOTTOK": "", "STRIPE_WEBHOOK_SECRET": "", "EMAIL_DAY_SECONDS": "86400",
                   "LEAD_MIN_SECONDS": "0", "LEAD_RATE_PER_IP": "1000", "LEAD_RATE_GLOBAL": "1000"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# una landing de la version anterior, para probar la importacion
(DATA / "pages").mkdir(parents=True, exist_ok=True)
(DATA / "pages" / "vieja-abc123.html").write_text(
    "<!DOCTYPE html><html><head><title>Landing vieja</title></head><body><h1>Hola</h1></body></html>", encoding="utf-8")
(DATA / "stats.json").write_text(json.dumps({"vieja-abc123": {"title": "Landing vieja", "created": 1700000000, "views": 7}}))
(DATA / "leads.jsonl").write_text(json.dumps({"name": "Ana", "email": "ana@ej.com", "slug": "vieja-abc123", "ts": 1700000100}) + "\n")


def post_lead(client, slug: str, **body):
    """Como un navegador: abre la pagina (recibe el token de la visita) y envia el formulario."""
    import re as _re
    html = client.get(f"/p/{slug}?preview=1").text
    token = _re.search(r'window.__la_t="([^"]+)"', html).group(1)
    return client.post("/api/lead", json={"slug": slug, "la_t": token, **body})


def pay(client, slug: str, item: str = "main"):
    """Como un comprador: abre el checkout de demostracion y pulsa "Simular pago aprobado"."""
    import re as _re
    href = _re.search(r'href="(/go/[^"]+/paid-[^"]+)"', client.get(f"/checkout/{slug}/{item}").text).group(1)
    return client.get(href.replace("&amp;", "&"), follow_redirects=False)

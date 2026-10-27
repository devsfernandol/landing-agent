"""Archivos HTML de las landings y su post-proceso (SEO, variante B, auditorias)."""
import re

from bs4 import BeautifulSoup
from pathlib import Path

from fastapi import HTTPException

from . import db, seo
from .config import PAGES, PUBLIC_URL


def page_file(slug: str, variant: str = "A") -> Path:
    return PAGES / (f"{slug}.html" if variant == "A" else f"{slug}.b.html")


def check_slug(slug: str) -> dict:
    landing = db.get_landing(slug) if re.fullmatch(r"[a-z0-9-]+", slug) else None
    if not landing or not page_file(slug).exists():
        raise HTTPException(404, "Landing no encontrada")
    return landing


def extract_html(text: str):
    """Devuelve (html, completo). Si el modelo se corto, repara el HTML en vez de fallar."""
    text = text.strip()
    fence = re.search(r"```(?:html)?\s*(.*?)(?:```|$)", text, re.S | re.I)
    if fence and "<" in fence.group(1):
        text = fence.group(1).strip()
    start = re.search(r"<!doctype html|<html", text, re.I)
    if not start:
        print("[landing-agent] respuesta sin HTML:", text[:300], flush=True)
        raise HTTPException(502, "El modelo no devolvio HTML. Intenta de nuevo.")
    text = text[start.start():]
    end = text.lower().rfind("</html>")
    if end != -1:
        return text[: end + len("</html>")], True
    # Cortado: quitar la etiqueta a medias y cerrar lo que quedo abierto
    if ">" in text:
        text = text[: text.rfind(">") + 1]
    low = text.lower()
    if low.rfind("<script") > low.rfind("</script>"):
        text += "\n</script>"
    if low.rfind("<style") > low.rfind("</style>"):
        text += "\n</style>"
    return text + "\n</body></html>", False


FALLBACK_FORM = """
<section id="formulario" style="max-width:520px;margin:48px auto;padding:24px;font-family:system-ui,sans-serif">
<h2>Reserva tu lugar</h2>
<form id="lf" style="display:grid;gap:12px">
<label>Nombre<br><input name="name" required style="width:100%;padding:10px"></label>
<label>Email<br><input name="email" type="email" required style="width:100%;padding:10px"></label>
<button type="submit" style="padding:12px;font-weight:700;cursor:pointer">Reservar mi lugar</button>
</form><p id="lm" role="status"></p>
<script>
document.getElementById('lf').onsubmit=async e=>{e.preventDefault();
const f=new FormData(e.target);
try{const r=await fetch('/api/lead',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({name:f.get('name'),email:f.get('email'),slug:'__SLUG__'})});
if(!r.ok)throw 0;e.target.style.display='none';
document.getElementById('lm').textContent='Listo, te avisaremos por email.'}
catch(_){document.getElementById('lm').textContent='No se pudo enviar. Revisa tu email e intenta de nuevo.'}}
</script></section>
"""

EXAMPLE_NOTE = '<p style="text-align:center;font-size:12px;opacity:.7;margin:12px 0">Testimonios de ejemplo.</p>'


def ensure_form(html: str) -> str:
    if "/api/lead" in html:
        return html
    i = html.lower().rfind("</body>")
    return html[:i] + FALLBACK_FORM + html[i:] if i != -1 else html + FALLBACK_FORM


def ensure_example_note(html: str) -> str:
    """Correccion determinista: los testimonios inventados siempre quedan marcados."""
    if "testimonios de ejemplo" in html.lower():
        return html
    i = html.lower().rfind("</body>")
    return html[:i] + EXAMPLE_NOTE + html[i:] if i != -1 else html + EXAMPLE_NOTE


def clean_placeholders(html: str, slug: str, has_logo: bool, n_imgs: int) -> str:
    if has_logo:
        html = html.replace("__LOGO__", f"/a/{slug}/logo.png")
    for i in range(1, n_imgs + 1):
        html = html.replace(f"__IMG{i}__", f"/a/{slug}/img{i}.jpg")
    # etiquetas <img> con marcadores que nadie subio: se quitan para no dejar imagenes rotas
    return re.sub(r"<img\b[^>]*__(?:LOGO|IMG\d+)__[^>]*>", "", html, flags=re.I)


def finalize(landing: dict, html: str) -> tuple[dict, dict]:
    """SEO tecnico + variante B + auditorias. Se ejecuta tras generar, ajustar, deshacer u optimizar."""
    slug, strategy = landing["slug"], landing.get("strategy")
    url = f"{PUBLIC_URL}/p/{slug}"
    image = f"{PUBLIC_URL}/a/{slug}/img1.jpg" if landing.get("imgs") else None
    logo = f"{PUBLIC_URL}/a/{slug}/logo.png" if landing.get("logo") else None
    html = ensure_example_note(ensure_form(html).replace("__SLUG__", slug))
    html = seo.optimize(html, strategy, url, image, logo)
    page_file(slug).write_text(html, encoding="utf-8")
    if strategy:
        # el titular A es el que esta publicado (un ajuste pudo cambiarlo)
        h1 = seo.h1_text(html)
        if h1 and h1 != strategy.get("titular_a"):
            strategy["titular_a"] = h1
            db.update_landing(slug, strategy=strategy)
    b = seo.make_variant(html, (strategy or {}).get("titular_b", ""))
    if b:
        page_file(slug, "B").write_text(b, encoding="utf-8")
    else:
        page_file(slug, "B").unlink(missing_ok=True)
    rep = seo.audit(html, (strategy or {}).get("seo", {}).get("keyword_principal", ""))
    conv = seo.conversion_audit(html, strategy, landing.get("brief", ""))
    db.update_landing(slug, seo_score=rep["score"], seo_report=rep, conv_report=conv)
    return rep, conv


# ---------- editor visual (GrapesJS) ----------
EDITOR_STYLE_ID = "la-editor"


def editor_source(html: str) -> dict:
    """Lo que carga el editor: el body sin scripts (el formulario y el tracking los conserva el servidor),
    todo el CSS del head y las hojas externas (fuentes) para que el lienzo se vea igual que la pagina publicada."""
    soup = BeautifulSoup(html, "html.parser")
    head = soup.head or soup
    css = "\n".join(st.get_text() for st in head.find_all("style"))
    links = [l["href"] for l in head.find_all("link", rel="stylesheet", href=True)]
    body = soup.body or soup
    for sc in body.find_all("script"):
        sc.decompose()
    return {"html": body.decode_contents(), "css": css, "styles": links}


def apply_editor(current: str, body_html: str, css: str) -> str:
    """Reemplaza el contenido del body por el del editor y agrega su CSS como capa final.
    Se conservan el head (SEO, JSON-LD, fuentes, estilos originales) y los scripts del body (envio del formulario)."""
    soup = BeautifulSoup(current, "html.parser")
    body = soup.body
    if body is None:
        raise ValueError("la landing no tiene <body>")
    scripts = [sc.extract() for sc in body.find_all("script")]
    new = BeautifulSoup(body_html, "html.parser")
    new = new.body or new
    for sc in new.find_all("script"):
        sc.decompose()
    for tag in new.find_all(True):  # sin manejadores inline: el comportamiento lo pone el servidor, no el editor
        for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
            del tag[attr]
    body.clear()
    for child in list(new.contents):
        body.append(child)
    for sc in scripts:
        body.append(sc)
    old = soup.find("style", id=EDITOR_STYLE_ID)
    if old:
        old.decompose()
    if css.strip() and soup.head:
        st = soup.new_tag("style", id=EDITOR_STYLE_ID)
        st.string = css.replace("</style", "<\\/style")
        soup.head.append(st)
    return str(soup)

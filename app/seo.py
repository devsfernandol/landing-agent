"""SEO tecnico de las landings: inyeccion de metadatos y JSON-LD, variante A/B, consentimiento y auditoria."""
import json
import re
import unicodedata
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup

COURSE_TYPES = {"curso", "mentoria", "masterclass"}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text)).strip()


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


# ---------- datos estructurados ----------
def json_ld(strategy: dict, url: str, image: str | None, logo: str | None) -> list[dict]:
    p, seo = strategy.get("producto", {}), strategy.get("seo", {})
    name = p.get("nombre") or strategy.get("titular_a", "")
    desc = p.get("descripcion") or seo.get("meta_description", "")
    brand = {"@type": "Organization", "name": p.get("marca") or name}
    if logo:
        brand["logo"] = logo
    item = {"@context": "https://schema.org", "name": name, "description": desc, "url": url}
    if p.get("tipo") in COURSE_TYPES:
        item.update({"@type": "Course", "provider": brand, "inLanguage": "es",
                     "hasCourseInstance": {"@type": "CourseInstance", "courseMode": "online"}})
    else:
        item.update({"@type": "Product", "brand": brand})
    if image:
        item["image"] = image
    if isinstance(p.get("precio"), (int, float)) and p["precio"] > 0:
        item["offers"] = {"@type": "Offer", "price": p["precio"], "priceCurrency": p.get("moneda") or "USD",
                          "availability": "https://schema.org/InStock", "url": url, "category": "Paid"}
    graph = [item]
    faq = [q for q in strategy.get("faq", []) if q.get("pregunta") and q.get("respuesta")]
    if faq:
        graph.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q["pregunta"], "acceptedAnswer": {"@type": "Answer", "text": q["respuesta"]}}
            for q in faq]})
    return graph


# ---------- inyeccion ----------
MANAGED = ["title", 'meta[name="description"]', 'meta[name="robots"]', 'link[rel="canonical"]',
           'meta[property^="og:"]', 'meta[name^="twitter:"]', 'script[type="application/ld+json"]']

CONSENT = ('<label class="la-consent" style="display:flex;gap:8px;align-items:flex-start;font-size:13px;line-height:1.4;text-align:left">'
           '<input type="checkbox" required style="margin-top:3px;width:auto"> '
           '<span>Acepto recibir informacion por email y la <a href="/privacidad" target="_blank" rel="noopener">politica de privacidad</a>.</span></label>')


def optimize(html: str, strategy: dict | None, url: str, image: str | None = None, logo: str | None = None) -> str:
    """Idempotente: se puede aplicar despues de cada ajuste sin duplicar etiquetas."""
    soup = _soup(html)
    if soup.html is None:
        return html
    soup.html["lang"] = soup.html.get("lang") or "es"
    head = soup.head
    if head is None:
        head = soup.new_tag("head")
        soup.html.insert(0, head)
    if not head.find("meta", attrs={"name": "viewport"}):
        head.insert(0, soup.new_tag("meta", attrs={"name": "viewport", "content": "width=device-width,initial-scale=1"}))
    if not head.find("meta", charset=True):
        head.insert(0, soup.new_tag("meta", charset="utf-8"))

    if strategy:
        for sel in MANAGED:
            for t in head.select(sel):
                t.decompose()
        seo = strategy.get("seo", {})
        title = _clip(seo.get("meta_title") or strategy.get("titular_a", ""), 60)
        desc = _clip(seo.get("meta_description") or strategy.get("producto", {}).get("descripcion", ""), 158)
        t = soup.new_tag("title")
        t.string = title
        tags = [t,
                soup.new_tag("meta", attrs={"name": "description", "content": desc}),
                soup.new_tag("meta", attrs={"name": "robots", "content": "index,follow,max-image-preview:large"}),
                soup.new_tag("link", rel="canonical", href=url)]
        og = {"og:type": "website", "og:locale": "es_ES", "og:title": title, "og:description": desc, "og:url": url,
              "og:site_name": strategy.get("producto", {}).get("marca") or title}
        if image or logo:
            og["og:image"] = image or logo
        tags += [soup.new_tag("meta", attrs={"property": k, "content": v}) for k, v in og.items()]
        tags += [soup.new_tag("meta", attrs={"name": "twitter:card", "content": "summary_large_image" if image else "summary"}),
                 soup.new_tag("meta", attrs={"name": "twitter:title", "content": title}),
                 soup.new_tag("meta", attrs={"name": "twitter:description", "content": desc})]
        ld = soup.new_tag("script", type="application/ld+json")
        ld.string = json.dumps(json_ld(strategy, url, image, logo), ensure_ascii=False)
        tags.append(ld)
        anchor = head.find("meta", attrs={"name": "viewport"})
        for tag in reversed(tags):
            anchor.insert_after(tag)

    # un unico H1: los sobrantes bajan a H2
    for extra in soup.find_all("h1")[1:]:
        extra.name = "h2"

    # imagenes: la primera carga con prioridad (LCP), el resto en diferido; nunca sin alt
    alt = (strategy or {}).get("producto", {}).get("nombre", "")
    for i, img in enumerate(soup.find_all("img")):
        if not img.get("alt"):
            img["alt"] = alt
        img["decoding"] = "async"
        if i == 0:
            img["fetchpriority"] = "high"
            img.attrs.pop("loading", None)
        else:
            img["loading"] = "lazy"

    # consentimiento explicito antes de enviar el email
    for form in soup.find_all("form"):
        if form.find("input", attrs={"type": "email"}) and not form.select_one(".la-consent"):
            box = _soup(CONSENT).label
            btn = form.find("button", attrs={"type": "submit"}) or (form.find_all("button") or [None])[-1]
            btn.insert_before(box) if btn else form.append(box)
    return str(soup)


def make_variant(html: str, headline: str) -> str | None:
    """Variante B: misma pagina con otro H1. Comparte canonical con A para no duplicar contenido."""
    soup = _soup(html)
    h1 = soup.find("h1")
    if not h1 or not headline:
        return None
    h1.clear()
    h1.string = headline
    return str(soup)


# ---------- auditoria ----------
def _has_kw(text: str, kw: str) -> bool:
    text, kw = _norm(text), _norm(kw)
    if not kw:
        return False
    if kw in text:
        return True
    words = [w for w in kw.split() if len(w) > 3]
    return bool(words) and sum(w[:5] in text for w in words) / len(words) >= 0.75


def audit(html: str, keyword: str = "") -> dict:
    soup = _soup(html)
    head = soup.head or soup
    title = head.title.get_text(strip=True) if head.title else ""
    md = head.find("meta", attrs={"name": "description"})
    desc = md.get("content", "") if md else ""
    h1s = soup.find_all("h1")
    h1 = h1s[0].get_text(" ", strip=True) if h1s else ""
    body = soup.body or soup
    for t in body(["script", "style", "noscript"]):
        t.extract()
    words = body.get_text(" ", strip=True).split()
    first = " ".join(words[:150])
    imgs = soup.find_all("img")
    ld_ok = False
    for s in _soup(html).find_all("script", type="application/ld+json"):
        try:
            json.loads(s.string or "")
            ld_ok = True
        except ValueError:
            ld_ok = False
            break
    og = {m.get("property") for m in head.find_all("meta", property=True)}
    kb = len(html.encode()) / 1024
    robots = head.find("meta", attrs={"name": "robots"})

    checks = [
        ("title", "Title de 30-60 caracteres", 30 <= len(title) <= 60, f"{len(title)} caracteres", 10),
        ("title_kw", "Keyword en el title", _has_kw(title, keyword), title or "sin title", 8),
        ("desc", "Meta description de 70-160 caracteres", 70 <= len(desc) <= 160, f"{len(desc)} caracteres", 10),
        ("desc_kw", "Keyword en la meta description", _has_kw(desc, keyword), "", 5),
        ("h1", "Un unico H1", len(h1s) == 1, f"{len(h1s)} H1 encontrados", 10),
        ("h1_kw", "Keyword en el H1", _has_kw(h1, keyword), h1, 6),
        ("h2", "Al menos 3 H2 descriptivos", len(soup.find_all("h2")) >= 3, f"{len(soup.find_all('h2'))} H2", 5),
        ("kw_first", "Keyword en las primeras 150 palabras", _has_kw(first, keyword), "", 6),
        ("words", "Contenido de 350+ palabras", len(words) >= 350, f"{len(words)} palabras", 5),
        ("canonical", "URL canonica", bool(head.find("link", rel="canonical")), "", 5),
        ("indexable", "Indexable (sin noindex)", not (robots and "noindex" in robots.get("content", "")), "", 5),
        ("og", "Open Graph completo", {"og:title", "og:description", "og:url"} <= og, "", 5),
        ("og_img", "Imagen para compartir (og:image)", "og:image" in og, "", 3),
        ("schema", "Datos estructurados JSON-LD validos", ld_ok, "Course/Product + FAQPage", 8),
        ("lang", "Idioma declarado", bool(soup.html and soup.html.get("lang")), "", 3),
        ("viewport", "Meta viewport (movil)", bool(head.find("meta", attrs={"name": "viewport"})), "", 3),
        ("alt", "Imagenes con texto alternativo", all(i.get("alt") for i in imgs), f"{len(imgs)} imagenes", 4),
        ("weight", "Pagina ligera (< 100 KB de HTML)", kb < 100, f"{kb:.1f} KB", 4),
    ]
    total = sum(c[4] for c in checks)
    got = sum(c[4] for c in checks if c[2])
    return {"score": round(100 * got / total), "keyword": keyword,
            "checks": [{"id": i, "label": l, "ok": ok, "detail": d, "weight": w} for i, l, ok, d, w in checks]}


def conversion_audit(html: str, strategy: dict | None, brief: str = "") -> dict:
    """Controles de conversion: lo que un infoproductor revisaria antes de mandar trafico."""
    s = strategy or {}
    has_guarantee = "garant" in _norm(brief)
    soup = _soup(html)
    for t in soup(["script", "style", "noscript"]):
        t.extract()
    text = _norm(soup.get_text(" ", strip=True))
    price = s.get("producto", {}).get("precio")
    faq = [q.get("pregunta", "") for q in s.get("faq", [])]
    faq_found = sum(_norm(q)[:30] in text for q in faq if q)
    ctas = soup.find_all(["button"]) + [a for a in soup.find_all("a") if a.get("href", "").startswith(("#", "http", "/"))]
    form_ok = "/api/lead" in html and any(f.find("input", attrs={"type": "email"}) for f in soup.find_all("form"))
    checks = [
        ("form", "Formulario de captura conectado", form_ok, ""),
        ("cta", "Al menos 3 llamadas a la accion", len(ctas) >= 3, f"{len(ctas)} CTA"),
        ("price", "Precio visible", not isinstance(price, (int, float)) or str(int(price)) in text.replace(" ", ""),f"{price}" if price else "sin precio en el brief"),
        ("guarantee", "Garantia del brief visible", not has_guarantee or "garant" in text, "" if has_guarantee else "el brief no indica garantia"),
        ("faq", "FAQ responde las objeciones", not faq or faq_found >= min(3, len(faq)), f"{faq_found}/{len(faq)} preguntas"),
        ("honesty", "Testimonios marcados como ejemplo", "testimonios de ejemplo" in text, ""),
    ]
    return {"passed": sum(c[2] for c in checks), "total": len(checks),
            "checks": [{"id": i, "label": l, "ok": ok, "detail": d} for i, l, ok, d in checks]}


def h1_text(html: str) -> str:
    h1 = _soup(html).find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


# ---------- sitemap y medicion ----------
def sitemap(urls: list[tuple[str, int]]) -> str:
    items = "".join(
        f"<url><loc>{escape(u)}</loc><lastmod>{datetime.fromtimestamp(ts, timezone.utc):%Y-%m-%d}</lastmod></url>"
        for u, ts in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'


def tracking(pixel: str, ga4: str, next_url: str = "", purchase: float | None = None, form_token: str = "",
             currency: str = "USD", purchase_id: str = "") -> str:
    """Pixel de Meta y GA4 opcionales; UTM y mc_id (ManyChat) viajan con el lead; tras el lead, siguiente paso del embudo.
    View Transitions: entre la landing y los pasos del embudo el navegador hace un fundido en vez de un parpadeo en blanco."""
    out = "<style>@media (prefers-reduced-motion:no-preference){@view-transition{navigation:auto}}</style>"
    if pixel:
        out += ("<script>!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};"
                "if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;"
                "s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');"
                f"fbq('init','{escape(pixel)}');fbq('track','PageView');</script>")
    if ga4:
        out += (f'<script async src="https://www.googletagmanager.com/gtag/js?id={escape(ga4)}"></script>'
                "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}"
                f"gtag('js',new Date());gtag('config','{escape(ga4)}');</script>")
    if purchase:
        cur = json.dumps(currency or "USD")
        eid = f",{{eventID:{json.dumps(purchase_id)}}}" if purchase_id else ""  # mismo id que la Conversions API
        out += (f"<script>window.fbq&&fbq('track','Purchase',{{value:{purchase},currency:{cur}}}{eid});"
                f"window.gtag&&gtag('event','purchase',{{value:{purchase},currency:{cur}}});</script>")
    if form_token:
        out += f"<script>window.__la_t={json.dumps(form_token)};</script>"
    out += ("<script>(function(){const q=new URLSearchParams(location.search),K='la_utm';let u={};"
            "try{u=JSON.parse(sessionStorage.getItem(K)||'{}')}catch(_){}"
            "['utm_source','utm_medium','utm_campaign','mc_id','fbclid'].forEach(k=>{if(q.get(k))u[k]=q.get(k)});"
            "try{sessionStorage.setItem(K,JSON.stringify(u))}catch(_){}"
            "const f=window.fetch;window.fetch=async function(url,o){const lead=String(url).indexOf('/api/lead')>-1;"
            "if(lead&&o&&typeof o.body==='string'){try{o=Object.assign({},o,{body:JSON.stringify(Object.assign(JSON.parse(o.body),u,{la_t:window.__la_t||''}))})}catch(_){}}"
            "const r=await f.call(this,url,o);"
            "try{if(lead&&r.ok){let d={};try{d=await r.clone().json()}catch(_){}"
            "window.fbq&&fbq('track','Lead',{},d.event_id?{eventID:d.event_id}:undefined);window.gtag&&gtag('event','generate_lead');"
            f"{'setTimeout(function(){location.href=' + json.dumps(next_url) + '},1800);' if next_url else ''}"
            "}}catch(_){}return r}})();</script>")
    return out

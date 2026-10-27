"""Pasos del embudo posteriores a la landing: checkout, upsell (OTO), downsell y gracias, mas sus metricas.

Recorrido:  landing --lead--> /go/checkout --pago--> /go/paid-main -> upsell --si--> /go/upsell-yes --pago--> gracias
                                                                            --no--> downsell --si/no--> gracias
Con links de pago reales (Stripe Payment Links, Hotmart) la URL de exito de cada producto debe ser /go/{slug}/paid-{item}:
asi la llegada al siguiente paso registra la compra. Sin links, un checkout de demostracion simula el pago.
"""
import math
import random
import re
from html import escape as e

STEPS = ("upsell", "downsell", "gracias")
ITEMS = ("main", "upsell", "downsell")

# accion -> (evento registrado, destino). Los destinos "checkout:x" se resuelven al link de pago o a la demo.
ROUTES = {
    "checkout": ("checkout", "checkout:main"),
    "paid-main": ("purchase_main", "/p/{slug}/upsell"),
    "upsell-yes": ("upsell_yes", "checkout:upsell"),
    "upsell-no": ("upsell_no", "/p/{slug}/downsell"),
    "paid-upsell": ("purchase_upsell", "/p/{slug}/gracias"),
    "downsell-yes": ("downsell_yes", "checkout:downsell"),
    "downsell-no": ("downsell_no", "/p/{slug}/gracias"),
    "paid-downsell": ("purchase_downsell", "/p/{slug}/gracias"),
    # el cliente abandona el checkout: sigue el embudo sin compra (y la recuperacion por email sigue activa)
    "cancel-main": ("checkout_cancel", "/p/{slug}"),
    "cancel-upsell": ("upsell_cancel", "/p/{slug}/downsell"),
    "cancel-downsell": ("downsell_cancel", "/p/{slug}/gracias"),
}


# ---------- identidad visual heredada de la landing ----------
def _lum(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _sat(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b)


def _hex6(v: str) -> str | None:
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", v.strip())
    if not m:
        return None
    h = m.group(1)
    return "#" + (h if len(h) == 6 else "".join(c * 2 for c in h)).lower()


def theme(html: str) -> dict:
    """Extrae colores y fuentes de la landing (variables CSS y reglas de body/h1). Con respaldo neutro."""
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I))
    raw = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", css))

    def resolve(v: str, depth: int = 0) -> str:
        if depth > 4:
            return v
        return re.sub(r"var\((--[\w-]+)[^)]*\)", lambda m: resolve(raw.get(m.group(1), ""), depth + 1), v).strip()

    colors = {k: h for k, v in raw.items() if (h := _hex6(resolve(v)))}

    def pick(pattern: str, fallback):
        for k, v in colors.items():
            if re.search(pattern, k, re.I):
                return v
        return fallback

    vals = list(colors.values())
    bg = pick(r"bg|fondo|paper|cream|base|surface|light", max(vals, key=_lum) if vals else "#ffffff")
    text = pick(r"ink|text|fg|dark|txt|negro", min(vals, key=_lum) if vals else "#17202b")
    accent = pick(r"accent|primary|brand|cta|main|acento|primario", max(vals, key=_sat) if vals else "#0b6bcb")
    if abs(_lum(bg) - _lum(text)) < 0.4:
        text = "#111111" if _lum(bg) > 0.4 else "#ffffff"

    def font(selector: str) -> str:
        m = re.search(selector + r"\s*\{[^}]*?font-family\s*:\s*([^;}]+)", css, re.I)
        if m:
            return resolve(m.group(1))
        m = re.search(selector + r"\s*\{[^}]*?font\s*:\s*[^;}]*?\d(?:px|rem|em)(?:/[\d.]+\w*)?\s+([^;}]+)", css, re.I)
        return resolve(m.group(1)) if m else ""

    body_font = font(r"(?<![\w-])body") or "system-ui, sans-serif"
    head_font = font(r"(?<![\w-])h1[^{]*") or body_font
    return {"bg": bg, "text": text, "accent": accent, "on_accent": "#111111" if _lum(accent) > 0.45 else "#ffffff",
            "body_font": body_font, "head_font": head_font,
            "font_links": "".join(re.findall(r"<link[^>]+fonts\.(?:googleapis|gstatic)\.com[^>]*>", html, re.I))}


# ---------- paginas ----------
def _shell(title: str, inner: str, t: dict, tracking: str = "") -> str:
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{e(title)}</title>{t['font_links']}
<style>
@media (prefers-reduced-motion:no-preference){{@view-transition{{navigation:auto}}}}
:root{{--bg:{t['bg']};--tx:{t['text']};--ac:{t['accent']};--on:{t['on_accent']}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:17px/1.6 {t['body_font']}}}
main{{max-width:680px;margin:0 auto;padding:32px 20px 60px}}
h1,h2{{font-family:{t['head_font']};line-height:1.2}}h1{{font-size:clamp(26px,5vw,38px);margin:12px 0}}
.step{{font-size:14px;padding:10px 14px;border-radius:10px;border:1px solid color-mix(in srgb,var(--tx) 18%,transparent);display:flex;gap:10px;align-items:center}}
.bar{{height:6px;border-radius:9px;background:color-mix(in srgb,var(--tx) 12%,transparent);overflow:hidden;margin-top:8px}}.bar i{{display:block;height:100%;background:var(--ac)}}
.card{{border:2px solid var(--ac);border-radius:14px;padding:22px;margin:22px 0;background:color-mix(in srgb,var(--ac) 6%,var(--bg))}}
.card ul{{padding:0;list-style:none;margin:12px 0}}.card li{{padding:6px 0 6px 28px;position:relative}}.card li:before{{content:"✓";position:absolute;left:0;color:var(--ac);font-weight:700}}
.price{{font-size:30px;font-weight:800}}.price s{{font-size:18px;opacity:.55;font-weight:400;margin-right:8px}}
.yes{{display:block;text-align:center;background:var(--ac);color:var(--on);font-weight:700;font-size:19px;padding:17px;border-radius:12px;text-decoration:none}}
.yes:hover{{filter:brightness(1.08)}}.no{{display:block;text-align:center;margin-top:16px;color:inherit;opacity:.7;font-size:15px}}
a:focus-visible{{outline:3px solid var(--ac);outline-offset:3px}}.muted{{opacity:.75;font-size:15px}}
ol li{{margin:8px 0}}.demo{{font-size:13px;opacity:.7;border-top:1px dashed currentColor;margin-top:28px;padding-top:12px}}
.field{{border:1px solid color-mix(in srgb,var(--tx) 25%,transparent);border-radius:8px;padding:12px;margin:8px 0;opacity:.6}}
</style>{tracking}</head><body><main>{inner}</main></body></html>"""


def _money(v) -> str:
    try:
        return f"{float(v):,.0f} USD".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def render_step(step: str, slug: str, funnel: dict, product: dict, t: dict, tracking: str = "",
                bought: list[str] | None = None) -> str:
    """bought: nombres de lo que compro el lead (None si no se sabe quien es, p. ej. llega desde otro dispositivo)."""
    if step == "upsell":
        u = funnel.get("upsell", {})
        anchor = f"<s>{_money(u.get('precio_ancla'))}</s>" if u.get("precio_ancla") else ""
        inner = f"""<div class="step">✓ <span>Tu compra de <b>{e(product.get('nombre', ''))}</b> esta confirmada. Paso 2 de 3: completa tu pedido.</span></div>
<div class="bar"><i style="width:66%"></i></div>
<h1>{e(u.get('titular', ''))}</h1><p>{e(u.get('subtitular', ''))}</p>
<div class="card"><h2 style="margin:0">{e(u.get('nombre', ''))}</h2><ul>{''.join(f'<li>{e(b)}</li>' for b in u.get('bullets', []))}</ul>
<p class="price">{anchor}{_money(u.get('precio'))}</p><p class="muted">{e(u.get('por_que_ahora', ''))}</p></div>
<a class="yes" href="/go/{slug}/upsell-yes">{e(u.get('cta_si') or 'Si, agregar a mi pedido')}</a>
<a class="no" href="/go/{slug}/upsell-no">{e(u.get('cta_no') or 'No, gracias')}</a>"""
        return _shell(u.get("nombre", "Oferta especial"), inner, t, tracking)
    if step == "downsell":
        d = funnel.get("downsell", {})
        inner = f"""<div class="step">Paso 2 de 3 · Una ultima opcion antes de terminar</div><div class="bar"><i style="width:80%"></i></div>
<h1>{e(d.get('titular', ''))}</h1><p>{e(d.get('subtitular', ''))}</p>
<div class="card"><h2 style="margin:0">{e(d.get('nombre', ''))}</h2><ul>{''.join(f'<li>{e(b)}</li>' for b in d.get('bullets', []))}</ul>
<p class="price">{_money(d.get('precio'))}</p></div>
<a class="yes" href="/go/{slug}/downsell-yes">{e(d.get('cta_si') or 'Si, lo quiero')}</a>
<a class="no" href="/go/{slug}/downsell-no">{e(d.get('cta_no') or 'No, gracias')}</a>"""
        return _shell(d.get("nombre", "Oferta"), inner, t, tracking)
    if bought == []:  # lead conocido que no compro nada: no se le agradece una compra que no hizo
        inner = f"""<div class="step">Tu pedido no se completo</div>
<h1>Tu lugar sigue disponible</h1><p>No se registro ningun pago, asi que no se te cobro nada.
Si quieres terminar tu inscripcion en <b>{e(product.get('nombre', ''))}</b>, puedes hacerlo cuando quieras.</p>
<a class="yes" href="/go/{slug}/checkout">Completar mi inscripcion</a>"""
        return _shell("Pedido sin completar", inner, t, tracking)
    g = funnel.get("gracias", {})
    order = (f"""<div class="card"><h2 style="margin:0 0 6px">Tu pedido</h2><ul>{''.join(f'<li>{e(b)}</li>' for b in bought)}</ul>
<p class="muted">Te enviamos el acceso a tu email.</p></div>""" if bought else "")
    inner = f"""<div class="step">✓ Pedido completo</div><div class="bar"><i style="width:100%"></i></div>
<h1>{e(g.get('titular') or 'Gracias por tu compra')}</h1><p>{e(g.get('mensaje', ''))}</p>{order}
<div class="card"><h2 style="margin:0 0 6px">Tus proximos pasos</h2><ol>{''.join(f'<li>{e(p)}</li>' for p in g.get('pasos', []))}</ol></div>"""
    return _shell("Gracias", inner, t, tracking)


def render_checkout(item: str, slug: str, funnel: dict, product: dict, t: dict, public_url: str, pay_token: str) -> str:
    """Checkout simulado para demostrar el recorrido completo sin cobrar."""
    if item == "main":
        name, price = product.get("nombre", ""), product.get("precio")
    else:
        name, price = funnel.get(item, {}).get("nombre", ""), funnel.get(item, {}).get("precio")
    inner = f"""<div class="step">🔒 Checkout de demostracion</div>
<h1>{e(name)}</h1><p class="price">{_money(price)}</p>
<div class="field">Email</div><div class="field">Tarjeta •••• •••• •••• 4242</div>
<a class="yes" href="/go/{slug}/paid-{item}?ck={e(pay_token)}">Simular pago aprobado</a>
<a class="no" href="/go/{slug}/cancel-{item}">Cancelar, no quiero comprar</a>
<p class="demo">En produccion este paso es un Stripe Payment Link o un checkout de Hotmart, con URL de exito
<code>{e(public_url)}/go/{slug}/paid-{item}</code>. Se configura en el panel, en "Links de pago".</p>"""
    return _shell(f"Checkout · {name}", inner, t)


def render_access(product: dict, t: dict) -> str:
    """Acceso de ejemplo mientras no haya un link real (Notion, Hotmart, Drive) configurado en el panel."""
    bullets = "".join(f"<li>{e(b)}</li>" for b in product.get("bullets", []))
    inner = f"""<div class="step">✓ Acceso confirmado</div>
<h1>{e(product.get('nombre', ''))}</h1><p>{e(product.get('descripcion', ''))}</p>
<div class="card"><h2 style="margin:0 0 6px">Tu contenido</h2>{f'<ul>{bullets}</ul>' if bullets else ''}
<p class="muted">Aqui se entrega el producto: un espacio de Notion, un curso en Hotmart o una carpeta de Drive.</p></div>
<p class="demo">Acceso de ejemplo. Se reemplaza en el panel, en "Entrega", pegando el link real de cada producto:
los emails de compra empiezan a usarlo de inmediato.</p>"""
    return _shell(f"Acceso · {product.get('nombre', '')}", inner, t)


# ---------- metricas ----------
def _rate(a: int, b: int):
    """None si no hay base o si el paso tiene mas eventos que el anterior (datos importados o de prueba sin visita medida)."""
    return round(100 * a / b, 1) if b and a <= b else None


def metrics(variants: dict, events: dict, funnel: dict | None, product: dict) -> dict:
    views = sum(v["views"] for v in variants.values())
    leads = sum(v["leads"] for v in variants.values())
    ev = lambda k: events.get(k, 0)
    f = funnel or {}
    prices = {"main": product.get("precio") or 0, "upsell": f.get("upsell", {}).get("precio") or 0,
              "downsell": f.get("downsell", {}).get("precio") or 0}
    buys = {i: max(0, ev(f"purchase_{i}") - ev(f"refund_{i}")) for i in ITEMS}  # los reembolsos no cuentan como venta
    revenue = sum(float(prices[i]) * buys[i] for i in ITEMS)
    steps = [
        {"label": "Visitas a la landing", "n": views, "rate": None},
        {"label": "Leads", "n": leads, "rate": _rate(leads, views), "of": "de las visitas"},
        {"label": "Clic al checkout", "n": ev("checkout"), "rate": _rate(ev("checkout"), leads), "of": "de los leads"},
        {"label": "Compra principal", "n": buys["main"], "rate": _rate(buys["main"], ev("checkout")), "of": "de los checkouts"},
        {"label": "Upsell comprado", "n": buys["upsell"], "rate": _rate(buys["upsell"], ev("upsell_view")), "of": "de quienes lo vieron"},
        {"label": "Downsell comprado", "n": buys["downsell"], "rate": _rate(buys["downsell"], ev("downsell_view")), "of": "de quienes lo vieron"},
    ]
    return {"steps": steps, "revenue": round(revenue, 2), "aov": round(revenue / buys["main"], 2) if buys["main"] else None,
            "prices": prices, "purchases": buys}


# ---------- trafico inteligente (multi-armed bandit) ----------
BANDIT_WARMUP = 50  # visitas totales repartidas 50/50 antes de que el algoritmo empiece a decidir


def _beta(rng, s: dict) -> float:
    leads = min(s["leads"], s["views"])  # un lead sin visita medida no debe inflar la tasa
    return rng.betavariate(1 + leads, 1 + s["views"] - leads)


def bandit_choice(v: dict, rng=random) -> str:
    """Thompson sampling: cada visitante nuevo va a la variante que gana en un sorteo ponderado por lo aprendido.
    La que convierte mejor recibe cada vez mas trafico, pero la otra sigue recibiendo algo por si los datos enganan."""
    a, b = v.get("A", {"views": 0, "leads": 0}), v.get("B", {"views": 0, "leads": 0})
    if a["views"] + b["views"] < BANDIT_WARMUP:
        return rng.choice("AB")
    return "B" if _beta(rng, b) > _beta(rng, a) else "A"


def bandit_share_b(v: dict, draws: int = 4000) -> float:
    """Probabilidad de que B sea mejor = porcentaje del trafico nuevo que va a B."""
    a, b = v.get("A", {"views": 0, "leads": 0}), v.get("B", {"views": 0, "leads": 0})
    if a["views"] + b["views"] < BANDIT_WARMUP:
        return 50.0
    rng = random.Random(0)  # estable entre recargas del panel
    return round(100 * sum(_beta(rng, b) > _beta(rng, a) for _ in range(draws)) / draws, 1)


def ab_summary(v: dict) -> dict:
    """Test de dos proporciones (z). Con menos de 100 visitas por variante no se declara ganador."""
    a, b = v.get("A", {"views": 0, "leads": 0}), v.get("B", {"views": 0, "leads": 0})
    share = bandit_share_b(v)
    total = a["views"] + b["views"]
    routing = (f"Tráfico inteligente: aprendiendo ({total}/{BANDIT_WARMUP} visitas repartidas 50/50)." if total < BANDIT_WARMUP
               else f"Tráfico inteligente: B recibe el {share:g}% de las visitas nuevas y A el {100 - share:g}%.")
    if not a["views"] or not b["views"]:
        return {"confidence": None, "winner": None, "message": "Aun sin datos suficientes en ambas variantes.",
                "traffic_b": share, "routing": routing}
    pa, pb = a["leads"] / a["views"], b["leads"] / b["views"]
    p = (a["leads"] + b["leads"]) / (a["views"] + b["views"])
    se = math.sqrt(p * (1 - p) * (1 / a["views"] + 1 / b["views"]))
    z = (pb - pa) / se if se else 0.0
    conf = round(100 * math.erf(abs(z) / math.sqrt(2)), 1)
    leader = "B" if pb > pa else "A"
    lift = (pb - pa) / pa * 100 if pa else None
    if min(a["views"], b["views"]) < 100:
        msg = f"Muestra pequena ({min(a['views'], b['views'])} visitas en la variante con menos). Espera al menos 100 por variante."
        winner = None
    elif conf >= 95:
        msg = f"Ganadora: {leader} con {conf}% de confianza."
        winner = leader
    else:
        msg = f"Va ganando {leader} con {conf}% de confianza (se necesita 95%)."
        winner = None
    return {"confidence": conf, "winner": winner, "leader": leader,
            "lift": round(lift, 1) if lift is not None else None, "message": msg, "traffic_b": share, "routing": routing}

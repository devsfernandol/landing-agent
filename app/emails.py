"""Emails del embudo: la app decide si corresponde enviar y genera el HTML con la identidad del embudo;
n8n solo programa y envia (Gmail). Asi la logica comercial vive en un solo lugar y tiene pruebas.

Secuencias:
  recovery  se registro y no compro: los 5 emails del agente (dia 0-7) con boton al checkout; se detiene al comprar
  access    compro un producto: entrega inmediata con boton de acceso (link real o pagina de ejemplo)
  checkin   3 dias despues de la compra principal: seguimiento y soporte
"""
import hashlib
import hmac
import time
from html import escape as e

from . import db
from .config import EMAIL_DAY_SECONDS, PUBLIC_URL
from .funnel import _lum, theme as landing_theme
from .pages import page_file

FIRST_EMAIL_DELAY = 1 / 24  # el primer email sale ~1 h despues del lead: antes esta en el checkout
CHECKIN_DAYS = 3
ITEM_NAMES = {"main": "producto principal", "upsell": "upsell", "downsell": "downsell"}


# ---------- tokens firmados (acceso y baja) ----------
def sign(*parts) -> str:
    msg = ".".join(str(p) for p in parts)
    return msg + "." + hmac.new(db.secret().encode(), msg.encode(), hashlib.sha256).hexdigest()[:20]


def pay_token(slug: str, item: str) -> str:
    """Lo emite solo el checkout de demostracion: sin el, /go/{slug}/paid-{item} no registra una compra."""
    return sign(slug, "pay", item, int(time.time()))


def pay_ok(token: str, slug: str, item: str, max_age: int = 3600) -> bool:
    p = verify(token) if token else None
    return bool(p and p[:3] == [slug, "pay", item] and p[3].isdigit() and time.time() - int(p[3]) < max_age)


def verify(token: str) -> list[str] | None:
    *parts, sig = token.split(".")
    if not parts:
        return None
    expected = sign(*parts).rsplit(".", 1)[1]
    return parts if hmac.compare_digest(sig, expected) else None


# ---------- calendario que n8n ejecuta ----------
DEMO_DAY_SECONDS = 60


def day_seconds() -> int:
    """Modo demo (desde el panel): un 'dia' de las secuencias dura un minuto. Aplica a los leads nuevos."""
    return DEMO_DAY_SECONDS if db.get_setting("email_demo", False) else EMAIL_DAY_SECONDS


def _at(ts: int, days: float) -> int:
    return int(ts + days * day_seconds())


def recovery_schedule(lead: dict, strategy: dict) -> list[dict]:
    mails = (strategy.get("assets") or {}).get("emails") or []
    return [{"kind": "recovery", "n": i, "send_at": _at(lead["ts"], max(float(m.get("dia") or 0), FIRST_EMAIL_DELAY))}
            for i, m in enumerate(mails)]


def purchase_schedule(item: str, ts: int) -> list[dict]:
    out = [{"kind": "access", "item": item, "send_at": ts}]
    if item == "main":
        out.append({"kind": "checkin", "send_at": _at(ts, CHECKIN_DAYS)})
    return out


# ---------- productos y links ----------
def product(landing: dict, item: str) -> dict:
    s, f = landing.get("strategy") or {}, landing.get("funnel") or {}
    if item == "main":
        p = s.get("producto", {})
        return {"nombre": p.get("nombre") or landing["title"], "descripcion": p.get("descripcion", ""), "bullets": []}
    x = f.get(item, {})
    return {"nombre": x.get("nombre", ITEM_NAMES[item]), "descripcion": x.get("subtitular", ""), "bullets": x.get("bullets", [])}


def access_url(landing: dict, lead_id: int, item: str) -> str:
    """Link real configurado en el panel (Notion, Hotmart, Drive...) o la pagina de acceso de ejemplo."""
    return (landing.get("delivery") or {}).get(item) or f"{PUBLIC_URL}/acceso/{sign(lead_id, landing['slug'], item)}"


def checkout_url(slug: str, lead_id: int) -> str:
    return f"{PUBLIC_URL}/go/{slug}/checkout?lt={sign(lead_id, slug)}"


def unsubscribe_url(lead_id: int) -> str:
    return f"{PUBLIC_URL}/baja/{sign(lead_id)}"


# ---------- plantilla ----------
def _palette(landing: dict) -> dict:
    t = landing_theme(page_file(landing["slug"]).read_text(encoding="utf-8"))
    light = _lum(t["bg"]) > 0.5
    return {**t, "outer": t["bg"], "card": "#ffffff" if light else t["bg"], "ink": t["text"],
            "line": "#e6e6e6" if light else "#3a3a3a"}


def render(landing: dict, lead_id: int, *, preheader: str, heading: str, paragraphs: list[str],
           button: tuple[str, str] | None = None, after: str = "") -> str:
    """HTML de email compatible: tablas, estilos en linea, 600 px, boton 'a prueba de balas'."""
    t = _palette(landing)
    font = t["body_font"].replace('"', "'")
    head_font = t["head_font"].replace('"', "'")
    logo = (f'<img src="{PUBLIC_URL}/a/{landing["slug"]}/logo.png" alt="{e(landing["title"])}" height="40" '
            f'style="display:block;height:40px;width:auto;border:0;margin:0 0 20px">' if landing.get("logo") else "")
    body = "".join(f'<p style="margin:0 0 16px;font:16px/1.6 {font};color:{t["ink"]}">{p}</p>' for p in paragraphs)
    btn = ""
    if button:
        text, url = button
        btn = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px"><tr>'
               f'<td bgcolor="{t["accent"]}" style="border-radius:10px">'
               f'<a href="{e(url)}" target="_blank" style="display:inline-block;padding:15px 28px;font:700 16px {font};'
               f'color:{t["on_accent"]};text-decoration:none;border-radius:10px">{e(text)}</a></td></tr></table>')
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{e(heading)}</title>{t['font_links']}</head>
<body style="margin:0;padding:0;background:{t['outer']}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{t['outer']}"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px">
<tr><td bgcolor="{t['accent']}" style="height:6px;line-height:6px;font-size:0;border-radius:12px 12px 0 0">&nbsp;</td></tr>
<tr><td bgcolor="{t['card']}" style="padding:32px 32px 12px;border:1px solid {t['line']};border-top:0;border-radius:0 0 12px 12px">
{logo}<h1 style="margin:0 0 18px;font:700 26px/1.25 {head_font};color:{t['ink']}">{e(heading)}</h1>
{body}{btn}{after}
</td></tr>
<tr><td style="padding:18px 8px;font:12px/1.5 {font};color:{t['text']};opacity:.7;text-align:center">
Recibes este email porque te registraste en «{e(landing['title'])}».<br>
<a href="{e(unsubscribe_url(lead_id))}" style="color:{t['text']}">Darme de baja</a></td></tr>
</table></td></tr></table></body></html>"""


# ---------- textos editables desde el panel ----------
# Plantillas de los emails de compra. {nombre} y {producto} se reemplazan al enviar; se pueden editar en el panel.
TEMPLATES = {
    "access": {"asunto": "Tu acceso a {producto}", "titulo": "Bienvenido a {producto}", "boton": "Acceder ahora",
               "cuerpo": "Hola {nombre},\nTu compra de {producto} esta confirmada. Ya tienes acceso:"},
    "checkin": {"asunto": "¿Como vas con {producto}?", "titulo": "¿Como vas?", "boton": "Continuar donde lo deje",
                "cuerpo": "Hola {nombre},\nHan pasado unos dias desde que entraste a {producto}. ¿Como vas?\n"
                          "Si todavia no empezaste, este es el orden que mejor funciona:"},
}
EDITABLE = {"recovery": ("dia", "asunto", "objetivo", "cuerpo", "boton"), "access": ("asunto", "titulo", "cuerpo", "boton"),
            "checkin": ("asunto", "titulo", "cuerpo", "boton")}


def template(landing: dict, kind: str) -> dict:
    custom = ((landing.get("strategy") or {}).get("emails_extra") or {}).get(kind) or {}
    return {**TEMPLATES[kind], **{k: v for k, v in custom.items() if v}}


def _fill(text: str, name: str, producto: str) -> str:
    if not name:
        text = text.replace(" {nombre}", "").replace("{nombre}", "")
    return text.replace("{nombre}", name).replace("{producto}", producto)


def apply_edit(strategy: dict, kind: str, n: int, fields: dict) -> dict:
    """Aplica una edicion del panel sobre la estrategia (sin guardar): recovery vive en assets.emails, el resto en emails_extra."""
    allowed = {k: v for k, v in fields.items() if k in EDITABLE[kind]}
    if kind == "recovery":
        mails = (strategy.setdefault("assets", {})).setdefault("emails", [])
        if not 0 <= n < len(mails):
            raise ValueError("no existe ese email en la secuencia")
        mails[n] = {**mails[n], **allowed}
    else:
        extra = strategy.setdefault("emails_extra", {})
        extra[kind] = {k: v for k, v in allowed.items() if v and v != TEMPLATES[kind].get(k)}  # vacio = plantilla original
    return strategy


def editable(landing: dict) -> list[dict]:
    """Lo que el panel muestra para editar: la secuencia de recuperacion y las plantillas de compra."""
    mails = ((landing.get("strategy") or {}).get("assets") or {}).get("emails") or []
    p_main = product(landing, "main")["nombre"]
    out = [{"kind": "recovery", "n": i, "label": f"Recuperacion {i + 1}", "group": "Se registro y no compro",
            "fields": {"dia": m.get("dia", 0), "asunto": m.get("asunto", ""), "objetivo": m.get("objetivo", ""),
                       "cuerpo": m.get("cuerpo", ""),
                       "boton": m.get("boton") or ("Completar mi inscripcion" if i < len(mails) - 1 else f"Unirme a {p_main}")}}
           for i, m in enumerate(mails)]
    out += [{"kind": k, "n": 0, "label": l, "group": "Compro", "fields": template(landing, k)}
            for k, l in (("access", "Acceso al producto"), ("checkin", f"Seguimiento dia {CHECKIN_DAYS}"))]
    return out


# ---------- contenido por tipo ----------
def _paragraphs(text: str, name: str) -> list[str]:
    parts = [e(p.strip()) for p in (text or "").split("\n") if p.strip()]
    return ([f"Hola {e(name)},"] if name else []) + parts


def build(kind: str, lead: dict, landing: dict, n: int = 0, item: str = "main", check: bool = True) -> dict:
    """Devuelve {send, reason, to, subject, html}. n8n envia solo si send es True.
    check=False omite las reglas de envio (vista previa en el panel)."""
    slug = landing["slug"]
    if check:
        bought = db.purchased_items(lead["id"], slug)
    else:
        bought = set() if kind == "recovery" else {"main", "upsell", "downsell"}
    base = {"to": lead["email"], "lead_id": lead["id"], "kind": kind}
    if check and lead.get("unsubscribed"):
        return {**base, "send": False, "reason": "se dio de baja"}
    s = landing.get("strategy") or {}
    p_main = product(landing, "main")

    if kind == "recovery":
        if "main" in bought:
            return {**base, "send": False, "reason": "ya compro: la secuencia de recuperacion se detiene"}
        mails = (s.get("assets") or {}).get("emails") or []
        if n >= len(mails):
            return {**base, "send": False, "reason": "no existe ese email en la secuencia"}
        m = mails[n]
        cta = m.get("boton") or ("Completar mi inscripcion" if n < len(mails) - 1 else f"Unirme a {p_main['nombre']}")
        html = render(landing, lead["id"], preheader=m.get("objetivo", ""), heading=m.get("titulo") or m.get("asunto", ""),
                      paragraphs=_paragraphs(m.get("cuerpo", ""), lead.get("name", "")),
                      button=(cta, checkout_url(slug, lead["id"])))
        return {**base, "send": True, "subject": m.get("asunto", p_main["nombre"]), "html": html}

    if kind == "access":
        if item not in bought:
            return {**base, "send": False, "reason": "no hay compra registrada de ese producto"}
        pr = product(landing, item)
        tpl = {k: _fill(v, lead.get("name") or "", pr["nombre"]) for k, v in template(landing, "access").items()}
        bullets = "".join(f"<li style='margin:0 0 6px'>{e(b)}</li>" for b in pr["bullets"])
        after = f"<ul style='margin:0 0 20px;padding-left:20px'>{bullets}</ul>" if bullets else ""
        paras = _paragraphs(tpl["cuerpo"], "") + ([e(pr["descripcion"])] if pr["descripcion"] else [])
        html = render(landing, lead["id"], preheader=tpl["asunto"], heading=tpl["titulo"],
                      paragraphs=paras, button=(tpl["boton"], access_url(landing, lead["id"], item)), after=after)
        return {**base, "send": True, "subject": tpl["asunto"], "html": html}

    if kind == "checkin":
        if "main" not in bought:
            return {**base, "send": False, "reason": "sin compra principal"}
        steps = ((landing.get("funnel") or {}).get("gracias") or {}).get("pasos") or []
        tpl = {k: _fill(v, lead.get("name") or "", p_main["nombre"]) for k, v in template(landing, "checkin").items()}
        paras = _paragraphs(tpl["cuerpo"], "")
        after = "<ol style='margin:0 0 20px;padding-left:20px'>" + "".join(f"<li style='margin:0 0 6px'>{e(x)}</li>" for x in steps) + "</ol>" if steps else ""
        after += "<p style='margin:0 0 16px'>¿Alguna duda? Responde este email y te ayudamos.</p>"
        html = render(landing, lead["id"], preheader="Tu progreso", heading=tpl["titulo"], paragraphs=paras,
                      button=(tpl["boton"], access_url(landing, lead["id"], "main")), after=after)
        return {**base, "send": True, "subject": tpl["asunto"], "html": html}

    return {**base, "send": False, "reason": "tipo de email desconocido"}

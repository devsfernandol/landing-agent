"""
Landing Agent: agente de IA que convierte un brief de infoproducto en un embudo de venta publicado.

Rutas:
  panel y API privada (HTTP Basic) .... /, /api/*, /estrategia/{slug}
  embudo publico ..................... /p/{slug} (landing A/B), /p/{slug}/upsell|downsell|gracias, /checkout/{slug}/{item}
  tracking de pasos ................... /go/{slug}/{accion}  (registra el evento y redirige)
  SEO ................................. /sitemap.xml, /robots.txt
  captura ............................. POST /api/lead  -> SQLite + n8n + ManyChat
"""
import csv
import json
from contextlib import asynccontextmanager
import io
import re
import secrets
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from PIL import Image, ImageOps
import segno
from pydantic import BaseModel, Field

from . import agent, antispam, capi, db, emails, funnel, integrations, optimizer, payments, report, seller, seo, webhooks
from .config import APP_PASSWORD, ASSETS, AUTOMATION_KEY, GA4_ID, PROVIDER, PUBLIC_URL, STRIPE_WEBHOOK_SECRET, HOTMART_HOTTOK, N8N_WEBHOOK_URL
from .pages import apply_editor, check_slug, editor_source, finalize, page_file
from .prompts import FRAMEWORKS, TONES



@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="Landing Agent", lifespan=lifespan)
security = HTTPBasic(auto_error=False)
PANEL = (Path(__file__).parent / "static" / "panel.html").read_text(encoding="utf-8")
EDITOR = (Path(__file__).parent / "static" / "editor.html").read_text(encoding="utf-8")
AB_COOKIE_DAYS = 30
SLUG_RE = re.compile(r"[a-z0-9-]+")


def require_auth(creds: HTTPBasicCredentials = Depends(security)):
    """Protege el panel y la generacion (gasta creditos de la API). Si APP_PASSWORD esta vacio, no pide clave."""
    if not APP_PASSWORD:
        return
    ok = creds and secrets.compare_digest(creds.password, APP_PASSWORD)
    if not ok:
        raise HTTPException(401, "Autenticacion requerida", headers={"WWW-Authenticate": "Basic"})


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:40].strip("-") or "landing"


# ---------- recursos de marca ----------
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


async def save_image(up: UploadFile, dest: Path, logo: bool = False) -> bool:
    data = await up.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, f"{up.filename} pesa mas de 8 MB")
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception:
        raise HTTPException(400, f"{up.filename} no es una imagen valida (usa PNG, JPG o WebP)")
    if logo:
        im = im.convert("RGBA")
        im.thumbnail((600, 600))
        im.save(dest.with_suffix(".png"), optimize=True)
    else:
        im = im.convert("RGB")
        im.thumbnail((1600, 1600))
        im.save(dest.with_suffix(".jpg"), quality=82, optimize=True, progressive=True)
    return True


def brand_brief(colors, n_imgs, has_logo, whatsapp) -> str:
    parts = []
    if colors:
        c1, c2, c3 = colors
        parts.append(
            f"PALETA OBLIGATORIA DEL CLIENTE (reemplaza la regla de inventar paleta): color principal {c1}, color de fondo {c2}, color de acento {c3}. "
            "Definelos como variables CSS (--primary, --bg, --accent) y usa EXACTAMENTE estos tres como base, con neutros o tonos derivados de ellos. "
            "El texto debe tener contraste suficiente sobre cada fondo: si hace falta, usa casi negro o blanco para el texto, nunca un color que no se lea."
        )
    if has_logo:
        parts.append('LOGO DEL CLIENTE: usa <img src="__LOGO__" alt="Logo"> en el encabezado (altura maxima 44px, ancho automatico) y repitelo en el footer. No lo recolorees.')
    if n_imgs:
        tags = ", ".join(f"__IMG{i}__" for i in range(1, n_imgs + 1))
        parts.append(
            f"IMAGENES DEL CLIENTE ({n_imgs}): usa cada una como <img src=\"{tags.split(', ')[0]}\"...> real, con las rutas {tags}, "
            "en el hero y en secciones de solucion u oferta, con alt descriptivo, width y height, object-fit:cover y esquinas redondeadas. "
            "Esto es una excepcion a la regla de no usar imagenes. No uses otras rutas de imagen."
        )
    if whatsapp:
        parts.append(
            f"WHATSAPP: ademas del formulario, agrega un boton secundario 'Escribir por WhatsApp' que abra https://wa.me/{whatsapp}?text= con un mensaje breve codificado sobre el producto (target _blank, rel noopener)."
        )
    return ("\n\n" + "\n".join(parts)) if parts else ""


def clean_url(u: str) -> str:
    u = (u or "").strip()
    if u and not re.match(r"^https://[^\s\"'<>]+$", u):
        raise HTTPException(400, "Los links de pago deben empezar con https://")
    return u


# ---------- agente ----------
@app.post("/api/generate", dependencies=[Depends(require_auth)])
async def generate(
    prompt: str = Form(..., min_length=10, max_length=2000),
    tono: str = Form(""),
    framework: str = Form(""),
    color1: str = Form(""), color2: str = Form(""), color3: str = Form(""),
    whatsapp: str = Form(""),
    checkout_main: str = Form(""), checkout_upsell: str = Form(""), checkout_downsell: str = Form(""),
    logo: Optional[UploadFile] = File(None),
    imagenes: list[UploadFile] = File(default=[]),
):
    """Valida y guarda los archivos aqui; el trabajo del agente corre en segundo plano y el panel sigue su traza."""
    if tono not in TONES:
        raise HTTPException(400, "Tono no valido")
    if framework and framework not in FRAMEWORKS:
        raise HTTPException(400, "Framework no valido")
    colors = None
    if color1 or color2 or color3:
        if not all(HEX.match(c) for c in (color1, color2, color3)):
            raise HTTPException(400, "La paleta necesita los 3 colores en formato #RRGGBB")
        colors = (color1.upper(), color2.upper(), color3.upper())
    wa = re.sub(r"\D", "", whatsapp)
    if wa and not 8 <= len(wa) <= 15:
        raise HTTPException(400, "WhatsApp: escribe el numero con codigo de pais, solo digitos (ej. 584121234567)")
    checkout = {"main": clean_url(checkout_main), "upsell": clean_url(checkout_upsell), "downsell": clean_url(checkout_downsell)}
    slug = f"{slugify(prompt)}-{uuid.uuid4().hex[:6]}"
    adir = ASSETS / slug
    has_logo, n_imgs = False, 0
    if logo is not None and logo.filename:
        adir.mkdir(exist_ok=True)
        has_logo = await save_image(logo, adir / "logo", logo=True)
    for up in [u for u in imagenes if u.filename][:4]:
        adir.mkdir(exist_ok=True)
        n_imgs += 1
        await save_image(up, adir / f"img{n_imgs}")
    params = {"prompt": prompt, "tono": tono, "framework": framework, "colors": list(colors) if colors else [],
              "brand": brand_brief(colors, n_imgs, has_logo, wa), "has_logo": has_logo, "n_imgs": n_imgs, "checkout": checkout}
    job = agent.start("generate", slug, lambda j: agent.generate(j, params), agent.TEAM)
    return {"job": job.id, "slug": slug, "provider": PROVIDER}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def job_status(job_id: str):
    job = agent.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado (el servidor se reinicio?)")
    return job.public()


@app.post("/api/landings/{slug}/seo", dependencies=[Depends(require_auth)])
async def optimize_existing(slug: str):
    landing = check_slug(slug)
    job = agent.start("optimize", slug, lambda j: agent.optimize(j, landing), agent.TEAM)
    return {"job": job.id, "slug": slug}


class RefineIn(BaseModel):
    slug: str
    instruction: str = Field(min_length=4, max_length=800)


@app.post("/api/refine", dependencies=[Depends(require_auth)])
async def refine(body: RefineIn):
    rep, conv = await optimizer.apply_instruction(check_slug(body.slug), body.instruction)
    return {"ok": True, "path": f"/p/{body.slug}", "seo_score": rep["score"], "conv": f"{conv['passed']}/{conv['total']}"}


@app.post("/api/undo/{slug}", dependencies=[Depends(require_auth)])
async def undo(slug: str):
    landing = check_slug(slug)
    prev = page_file(slug).with_suffix(".prev.html")
    if not prev.exists():
        raise HTTPException(404, "No hay version anterior")
    html = prev.read_text(encoding="utf-8")
    prev.unlink()
    rep, _ = finalize(landing, html)
    return {"ok": True, "path": f"/p/{slug}", "seo_score": rep["score"]}


class ChatMsg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatIn(BaseModel):
    messages: list[ChatMsg] = Field(min_length=1, max_length=20)


@app.post("/api/landings/{slug}/chat", dependencies=[Depends(require_auth)])
async def chat(slug: str, body: ChatIn):
    """Agente optimizador: corre en segundo plano y el panel sigue su traza (que herramientas decide usar)."""
    check_slug(slug)
    if body.messages[-1].role != "user":
        raise HTTPException(400, "El ultimo mensaje debe ser del usuario")
    msgs = [m.model_dump() for m in body.messages]
    job = agent.start("chat", slug, lambda j: optimizer.chat(j, slug, msgs), ("optimizador",))
    return {"job": job.id}


class SettingsIn(BaseModel):
    email_demo: bool


@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings():
    return {"email_demo": db.get_setting("email_demo", False), "day_seconds": emails.day_seconds()}


@app.put("/api/settings", dependencies=[Depends(require_auth)])
async def put_settings(body: SettingsIn):
    db.set_setting("email_demo", body.email_demo)
    return {"email_demo": body.email_demo, "day_seconds": emails.day_seconds()}


class CheckoutIn(BaseModel):
    main: str = ""
    upsell: str = ""
    downsell: str = ""


@app.put("/api/landings/{slug}/checkout", dependencies=[Depends(require_auth)])
async def set_checkout(slug: str, body: CheckoutIn):
    check_slug(slug)
    links = {k: clean_url(v) for k, v in body.model_dump().items()}
    db.update_landing(slug, checkout=links)
    return {"ok": True, "checkout": links,
            "success_urls": {i: f"{PUBLIC_URL}/go/{slug}/paid-{i}" for i in funnel.ITEMS}}


@app.put("/api/landings/{slug}/delivery", dependencies=[Depends(require_auth)])
async def set_delivery(slug: str, body: CheckoutIn):
    """Link de acceso por producto (Notion, Hotmart, Drive...). Vacio = pagina de acceso de ejemplo."""
    check_slug(slug)
    links = {k: clean_url(v) for k, v in body.model_dump().items()}
    db.update_landing(slug, delivery=links)
    return {"ok": True, "delivery": links}


@app.get("/api/landings/{slug}/email-preview", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def email_preview(slug: str, kind: str = "recovery", n: int = 0, item: str = "main"):
    """Vista previa de cada email con un lead ficticio, sin enviar nada."""
    landing = check_slug(slug)
    lead = {"id": 0, "name": "Ana", "email": "ana@ejemplo.com", "unsubscribed": 0}
    out = emails.build(kind, lead, landing, n, item, check=False)
    if not out["send"]:
        raise HTTPException(404, out["reason"])
    return out["html"]


# ---------- edicion manual: editor visual de la landing y textos de los emails ----------
@app.get("/editor/{slug}", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def editor_page(slug: str):
    check_slug(slug)
    return HTMLResponse(EDITOR, headers={"Cache-Control": "no-store"})


@app.get("/api/landings/{slug}/editor", dependencies=[Depends(require_auth)])
async def editor_load(slug: str):
    landing = check_slug(slug)
    return {"title": landing["title"], **editor_source(page_file(slug).read_text(encoding="utf-8"))}


class EditorIn(BaseModel):
    html: str = Field(min_length=20, max_length=1_500_000)
    css: str = Field(default="", max_length=1_500_000)


@app.put("/api/landings/{slug}/html", dependencies=[Depends(require_auth)])
async def editor_save(slug: str, body: EditorIn):
    """Guarda lo editado a mano: queda una copia para Deshacer y se vuelven a correr SEO, variante B y auditorias."""
    landing = check_slug(slug)
    f = page_file(slug)
    current = f.read_text(encoding="utf-8")
    try:
        html = apply_editor(current, body.html, body.css)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    f.with_suffix(".prev.html").write_text(current, encoding="utf-8")
    rep, conv = finalize(landing, html)
    return {"ok": True, "seo_score": rep["score"], "conv": f"{conv['passed']}/{conv['total']}",
            "conv_failed": [c["label"] for c in conv.get("checks", []) if not c.get("ok")]}


class EmailEdit(BaseModel):
    kind: str = Field(pattern="^(recovery|access|checkin)$")
    n: int = Field(default=0, ge=0, le=20)
    item: str = Field(default="main", pattern="^(main|upsell|downsell)$")
    dia: Optional[float] = Field(default=None, ge=0, le=60)
    asunto: str = Field(default="", max_length=200)
    titulo: str = Field(default="", max_length=200)
    objetivo: str = Field(default="", max_length=300)
    cuerpo: str = Field(default="", max_length=6000)
    boton: str = Field(default="", max_length=80)


def _edited(landing: dict, body: EmailEdit) -> dict:
    fields = body.model_dump(exclude={"kind", "n", "item"}, exclude_none=True)
    if body.kind == "recovery" and not (fields.get("asunto", "").strip() and fields.get("cuerpo", "").strip()):
        raise HTTPException(400, "El asunto y el texto del email no pueden quedar vacios")
    try:
        strategy = emails.apply_edit(json.loads(json.dumps(landing.get("strategy") or {})), body.kind, body.n, fields)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {**landing, "strategy": strategy}


@app.get("/api/landings/{slug}/emails", dependencies=[Depends(require_auth)])
async def emails_list(slug: str):
    return {"emails": emails.editable(check_slug(slug))}


@app.post("/api/landings/{slug}/emails/preview", dependencies=[Depends(require_auth)])
async def emails_preview_draft(slug: str, body: EmailEdit):
    """Vista previa en vivo mientras se escribe: aplica el borrador sin guardarlo."""
    landing = _edited(check_slug(slug), body)
    lead = {"id": 0, "name": "Ana", "email": "ana@ejemplo.com", "unsubscribed": 0}
    out = emails.build(body.kind, lead, landing, body.n, body.item, check=False)
    if not out["send"]:
        raise HTTPException(404, out["reason"])
    return {"subject": out["subject"], "html": out["html"]}


@app.put("/api/landings/{slug}/emails", dependencies=[Depends(require_auth)])
async def emails_save(slug: str, body: EmailEdit):
    """Los textos se leen al momento de enviar: el cambio aplica tambien a los emails ya programados en n8n."""
    landing = _edited(check_slug(slug), body)
    db.update_landing(slug, strategy=landing["strategy"])
    return {"ok": True, "emails": emails.editable(landing)}


# ---------- integraciones: webhooks salientes para el CRM ----------
class HookIn(BaseModel):
    id: str = Field(default="", max_length=40)
    name: str = Field(default="Webhook", max_length=60)
    url: str = Field(pattern=r"^https?://", max_length=500)
    events: list[str] = Field(default_factory=list, max_length=10)
    active: bool = True
    headers: dict[str, str] = Field(default_factory=dict, max_length=5)


class HooksIn(BaseModel):
    hooks: list[HookIn] = Field(default_factory=list, max_length=10)


def _public_hooks(items: list[dict]) -> list[dict]:
    """El secreto se muestra para poder verificar la firma; las cabeceras (claves) se ocultan."""
    return [{**h, "headers": {k: "••••" for k in h.get("headers", {})}} for h in items]


@app.get("/api/webhooks", dependencies=[Depends(require_auth)])
async def webhooks_get():
    deliveries = [{**d, "email": mask_email(d["email"]) if "@" in d["email"] else d["email"]} for d in db.list_deliveries()]
    return {"hooks": _public_hooks(webhooks.hooks()), "events": webhooks.EVENTS, "deliveries": deliveries}


@app.put("/api/webhooks", dependencies=[Depends(require_auth)])
async def webhooks_put(body: HooksIn):
    old = {h["id"]: h for h in webhooks.hooks()}
    items = []
    for h in body.hooks:
        d = h.model_dump()
        if h.id in old:  # "••••" = la cabecera no se cambio en el panel
            d["headers"] = {k: (old[h.id].get("headers", {}).get(k, "") if v == "••••" else v) for k, v in d["headers"].items()}
        items.append(d)
    return {"hooks": _public_hooks(webhooks.save(items))}


class CapiIn(BaseModel):
    pixel_id: str = Field(default="", pattern=r"^\d{0,20}$")
    token: str = Field(default="", max_length=500)
    test_code: str = Field(default="", max_length=40)
    active: bool = True


@app.get("/api/meta-capi", dependencies=[Depends(require_auth)])
async def capi_get():
    c = capi.config()
    return {**c, "token": "••••" if c["token"] else "", "enabled": capi.enabled()}


@app.put("/api/meta-capi", dependencies=[Depends(require_auth)])
async def capi_put(body: CapiIn):
    old = db.get_setting("meta_capi", {}) or {}
    data = body.model_dump()
    if data["token"] == "••••":
        data["token"] = old.get("token", "")
    db.set_setting("meta_capi", data)
    return await capi_get()


@app.post("/api/meta-capi/test", dependencies=[Depends(require_auth)])
async def capi_test():
    """Envia un Lead de prueba: con el codigo de prueba aparece en vivo en Administrador de eventos > Probar eventos."""
    c = capi.config()
    if not (c["pixel_id"] and c["token"]):
        raise HTTPException(400, "Falta el ID del pixel o el token de acceso")
    lead = {"id": 0, "email": "prueba@ejemplo.com", "name": "Prueba", "ip": "", "ua": "landing-agent/prueba"}
    return await capi.send(capi.event("Lead", f"lead.prueba.{int(time.time())}", lead, PUBLIC_URL))


N8N_CRM_PATH = "landing-agent-crm"  # el workflow que despliega automations/deploy_n8n_crm.py


@app.post("/api/webhooks/n8n", dependencies=[Depends(require_auth)])
async def webhooks_connect_n8n():
    """Un clic: crea (o actualiza) el webhook hacia el workflow de CRM de tu n8n, con su clave, sin copiar nada."""
    if not (N8N_WEBHOOK_URL and AUTOMATION_KEY) or "/webhook/" not in N8N_WEBHOOK_URL:
        raise HTTPException(400, "Falta configurar n8n (N8N_WEBHOOK_URL y AUTOMATION_KEY)")
    url = N8N_WEBHOOK_URL.split("/webhook/")[0] + f"/webhook/{N8N_CRM_PATH}"
    items = [h for h in webhooks.hooks() if h["url"] != url]
    items.append({"name": "n8n → CRM", "url": url, "events": list(webhooks.EVENTS), "active": True,
                  "headers": {"X-Automation-Key": AUTOMATION_KEY}})
    saved = webhooks.save(items)  # los existentes conservan su id y su secreto
    return {"hooks": _public_hooks(saved), "url": url}


@app.post("/api/webhooks/{hook_id}/test", dependencies=[Depends(require_auth)])
async def webhooks_test(hook_id: str):
    hook = next((h for h in webhooks.hooks() if h["id"] == hook_id), None)
    if not hook:
        raise HTTPException(404, "Webhook no encontrado")
    sample = {"slug": "ejemplo", "title": "Landing de ejemplo"}
    lead = {"id": 0, "email": "prueba@ejemplo.com", "name": "Ana Prueba", "variant": "A", "utm_source": "panel"}
    ok = await webhooks.deliver(hook, webhooks.contact("lead.created", sample, lead, extra={"test": True}), retries=(0,))
    last = db.list_deliveries(1)[0]
    return {"ok": ok, "status": last["status"], "error": last["error"]}


# ---------- demo en vivo: QR para que el publico se registre desde su movil ----------
def demo_url(slug: str) -> str:
    return f"{PUBLIC_URL}/p/{slug}?utm_source=qr&utm_medium=demo-en-vivo"


@app.get("/api/landings/{slug}/qr.svg", dependencies=[Depends(require_auth)])
async def landing_qr(slug: str):
    check_slug(slug)
    buf = io.BytesIO()
    segno.make(demo_url(slug), error="m").save(buf, kind="svg", scale=10, border=2, dark="#111111", light="#ffffff",
                                               xmldecl=False, svgns=True, title="Codigo QR de la landing")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def mask_email(email: str) -> str:
    """En pantalla compartida no se muestra el email completo de nadie."""
    user, _, domain = email.partition("@")
    return (user[:2] + "***@" + domain) if domain else "***"


@app.get("/api/landings/{slug}/live", dependencies=[Depends(require_auth)])
async def landing_live(slug: str, since: int = 0):
    check_slug(slug)
    since = since or int(time.time())
    act = db.activity_since(slug, since)
    for lead in act["leads"]:
        lead["email"] = mask_email(lead["email"])
    v = db.variant_stats(slug)
    return {"now": int(time.time()), "url": demo_url(slug), "email_demo": emails.day_seconds() < 86400,
            "views": sum(x["views"] for x in v.values()), **act}


# ---------- A/B ----------
class ABIn(BaseModel):
    active: bool


@app.post("/api/landings/{slug}/ab", dependencies=[Depends(require_auth)])
async def toggle_ab(slug: str, body: ABIn):
    check_slug(slug)
    if body.active and not page_file(slug, "B").exists():
        raise HTTPException(400, "Esta landing no tiene variante B. Usa Optimizar con el agente para generar la estrategia.")
    db.update_landing(slug, ab_active=int(body.active))
    return {"ok": True, "active": body.active}


@app.post("/api/landings/{slug}/ab/promote", dependencies=[Depends(require_auth)])
async def promote_b(slug: str):
    """B pasa a ser la version principal; A queda como retadora con sus datos intactos."""
    landing = check_slug(slug)
    s = landing["strategy"] or {}
    if not page_file(slug, "B").exists() or not s.get("titular_b"):
        raise HTTPException(400, "No hay variante B")
    s["titular_a"], s["titular_b"] = s["titular_b"], s["titular_a"]
    s["angulo_a"], s["angulo_b"] = s.get("angulo_b", ""), s.get("angulo_a", "")
    db.update_landing(slug, strategy=s, ab_active=0)
    db.swap_variants(slug)
    finalize(db.get_landing(slug), page_file(slug, "B").read_text(encoding="utf-8"))
    return {"ok": True}


# ---------- consultas del panel ----------
def landing_summary(r: dict) -> dict:
    s = r.get("strategy") or {}
    r["variants"] = r.get("variants") or db.variant_stats(r["slug"])
    r["metrics"] = funnel.metrics(r["variants"], db.event_counts(r["slug"]), r.get("funnel"), s.get("producto", {}))
    r["channels"] = db.channel_counts(r["slug"])
    r["ab"] = funnel.ab_summary(r["variants"]) if s.get("titular_b") else None
    r["url"] = f"{PUBLIC_URL}/p/{r['slug']}"
    return r


@app.get("/api/landings", dependencies=[Depends(require_auth)])
async def landings():
    rows = []
    for r in db.list_landings():
        if not page_file(r["slug"]).exists():
            continue
        r = landing_summary(r)
        s = r.pop("strategy") or {}
        for k in ("seo_report", "conv_report", "trace"):
            r.pop(k, None)
        r["critic"] = (r.get("critic") or {}).get("promedio")
        r["usd"] = (r.pop("usage", None) or {}).get("usd")
        f = r.pop("funnel") or {}
        r.update(has_strategy=bool(s), has_funnel=bool(f.get("upsell")), keyword=s.get("seo", {}).get("keyword_principal"),
                 titular_a=s.get("titular_a"), titular_b=s.get("titular_b"),
                 views=r["metrics"]["steps"][0]["n"], leads=r["metrics"]["steps"][1]["n"])
        rows.append(r)
    return rows


@app.get("/api/landings/{slug}", dependencies=[Depends(require_auth)])
async def landing_detail(slug: str):
    return landing_summary(check_slug(slug))


@app.get("/api/landings/{slug}/strategy", dependencies=[Depends(require_auth)])
async def strategy_json(slug: str):
    landing = check_slug(slug)
    return {k: landing[k] for k in ("strategy", "funnel", "seo_report", "conv_report", "critic", "trace")}


@app.get("/estrategia/{slug}", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def strategy_report(slug: str):
    return report.render(landing_summary(check_slug(slug)))


@app.delete("/api/landings/{slug}", dependencies=[Depends(require_auth)])
async def delete_landing(slug: str):
    check_slug(slug)
    for v in ("A", "B"):
        page_file(slug, v).unlink(missing_ok=True)
    page_file(slug).with_suffix(".prev.html").unlink(missing_ok=True)
    adir = ASSETS / slug
    if adir.exists():
        for x in adir.iterdir():
            x.unlink()
        adir.rmdir()
    db.delete_landing(slug)
    return {"ok": True}


# ---------- embudo publico ----------
def _with_tracking(html: str, snippet: str) -> str:
    i = html.lower().find("</head>")
    return html[:i] + snippet + html[i:] if i != -1 else snippet + html


@app.get("/p/{slug}", response_class=HTMLResponse)
async def page(slug: str, request: Request, preview: int = 0, v: str = "", mc_id: str = ""):
    landing = check_slug(slug)
    cookie = f"ab_{slug}"
    has_b = landing["ab_active"] and page_file(slug, "B").exists()
    if preview:  # la vista previa del panel no cuenta como visita
        variant = "B" if v.upper() == "B" and page_file(slug, "B").exists() else "A"
    else:
        variant = request.cookies.get(cookie) if has_b else "A"  # el visitante que vuelve ve siempre la misma
        if variant not in ("A", "B"):
            variant = funnel.bandit_choice(db.variant_stats(slug))
        db.add_view(slug, variant)
    html = page_file(slug, variant).read_text(encoding="utf-8")
    next_url = f"/go/{slug}/checkout" if (landing.get("funnel") or {}).get("upsell") else ""
    snippet = seo.tracking(capi.pixel_id(), GA4_ID, next_url, form_token=antispam.form_token(slug))
    s = landing.get("strategy") or {}
    if s.get("producto") and landing.get("seller_chat"):  # agente vendedor: solo con estrategia (sabe de que habla)
        snippet += seller.widget(slug, s["producto"].get("nombre") or landing["title"], funnel.theme(html),
                                 [q.get("pregunta", "") for q in s.get("faq", []) if q.get("pregunta")], bool(preview))
    resp = HTMLResponse(_with_tracking(html, snippet), headers={"Cache-Control": "no-store"})
    if has_b and not preview:
        resp.set_cookie(cookie, variant, max_age=AB_COOKIE_DAYS * 86400, samesite="lax")
    if mc_id.isdigit():  # el id de ManyChat acompana al visitante por todo el embudo
        resp.set_cookie(f"mc_{slug}", mc_id, max_age=AB_COOKIE_DAYS * 86400, samesite="lax")
    return resp


def _funnel_ctx(slug: str):
    landing = check_slug(slug)
    f = landing.get("funnel") or {}
    if not f.get("upsell"):
        raise HTTPException(404, "Esta landing no tiene embudo")
    product = (landing.get("strategy") or {}).get("producto", {})
    return landing, f, product, funnel.theme(page_file(slug).read_text(encoding="utf-8"))


@app.get("/p/{slug}/{step}", response_class=HTMLResponse)
async def funnel_step(slug: str, step: str, request: Request, preview: int = 0):
    if step not in funnel.STEPS:
        raise HTTPException(404)
    landing, f, product, t = _funnel_ctx(slug)
    if not preview:
        db.add_event(slug, {"gracias": "thanks"}.get(step, step) + "_view")
    bought = None
    if step == "gracias" and not preview and (lead := lead_from_cookie(request, slug)):
        items = db.purchased_items(lead["id"], slug)
        bought = [emails.product(landing, i)["nombre"] for i in funnel.ITEMS if i in items]
        if not bought and payment_verified(landing, "main"):
            bought = None  # el webhook de la pasarela puede llegar unos segundos despues de la redireccion
    purchase = product.get("precio") if step == "upsell" and not preview else None
    buyer = lead_from_cookie(request, slug) if purchase else None
    snippet = seo.tracking(capi.pixel_id(), GA4_ID, purchase=purchase, currency=product.get("moneda") or "USD",
                           purchase_id=f"purchase.{buyer['id']}.main" if buyer else "") if not preview else ""
    return HTMLResponse(funnel.render_step(step, slug, f, product, t, snippet, bought), headers={"Cache-Control": "no-store"})


@app.get("/checkout/{slug}/{item}", response_class=HTMLResponse)
async def demo_checkout(slug: str, item: str):
    if item not in funnel.ITEMS:
        raise HTTPException(404)
    landing, f, product, t = _funnel_ctx(slug)
    return HTMLResponse(funnel.render_checkout(item, slug, f, product, t, PUBLIC_URL, emails.pay_token(slug, item)),
                        headers={"Cache-Control": "no-store"})


def lead_from_cookie(request: Request, slug: str):
    parts = emails.verify(request.cookies.get(f"lead_{slug}", ""))
    return db.get_lead(int(parts[0])) if parts and parts[1:] == [slug] else None


def payment_verified(landing: dict, item: str) -> bool:
    """Con el webhook de la pasarela configurado, la compra la confirma Stripe o Hotmart, no la URL de exito."""
    prov = payments.provider((landing.get("checkout") or {}).get(item, ""))
    return (prov == "stripe" and bool(STRIPE_WEBHOOK_SECRET)) or (prov == "hotmart" and bool(HOTMART_HOTTOK))


def record_purchase(landing: dict, item: str, lead: dict | None, mc_id: str, background: BackgroundTasks) -> bool:
    """Registra la compra una sola vez y avisa a ManyChat y a n8n (que programa el email de acceso). True si es nueva."""
    slug, event = landing["slug"], f"purchase_{item}"
    new = lead is not None and db.add_purchase(lead["id"], slug, item)
    if lead is not None and not new:
        return False  # recargar la pagina de exito o un reintento del webhook no duplica la compra
    db.add_event(slug, event)
    background.add_task(integrations.manychat_tag, mc_id, event)
    payload = {"type": event, "slug": slug, "landing": landing["title"], "mc_id": mc_id, "ts": int(time.time()),
               "api_base": PUBLIC_URL}
    if lead:
        payload.update(lead_id=lead["id"], email=lead["email"], name=lead["name"],
                       schedule=emails.purchase_schedule(item, payload["ts"]))
    background.add_task(integrations.send_n8n, payload)
    background.add_task(webhooks.emit, "purchase.completed", webhooks.contact("purchase.completed", landing, lead, item))
    price, currency = webhooks._price(landing, item)
    background.add_task(capi.track, "Purchase", f"purchase.{lead['id'] if lead else 0}.{item}", lead, f"{PUBLIC_URL}/p/{slug}",
                        {"value": price, "currency": currency, "content_name": emails.product(landing, item)["nombre"],
                         "content_ids": [f"{slug}:{item}"], "content_type": "product"})
    return True


@app.get("/go/{slug}/{action}")
async def funnel_go(slug: str, action: str, request: Request, background: BackgroundTasks, lt: str = "", ck: str = ""):
    if action not in funnel.ROUTES:
        raise HTTPException(404)
    landing = check_slug(slug)
    event, dest = funnel.ROUTES[action]
    p = emails.verify(lt) if lt else None  # link de un email: identifica al lead aunque abra en otro dispositivo
    token = lt if p and p[1:] == [slug] else ""
    lead = db.get_lead(int(p[0])) if token else lead_from_cookie(request, slug)
    mc_id = request.cookies.get(f"mc_{slug}", "")
    if event.startswith("purchase_"):
        item = event.removeprefix("purchase_")
        if payment_verified(landing, item):
            pass  # la registra el webhook de la pasarela; aqui el cliente solo sigue al siguiente paso
        elif (landing.get("checkout") or {}).get(item) or emails.pay_ok(ck, slug, item):
            record_purchase(landing, item, lead, mc_id, background)
        else:  # checkout de demostracion: sin el token de su boton, escribir esta URL a mano no es una compra
            return RedirectResponse(f"/checkout/{slug}/{item}", status_code=303)
    else:
        db.add_event(slug, event)
    dest = dest.format(slug=slug)
    if dest.startswith("checkout:"):
        item = dest.split(":", 1)[1]
        real = (landing.get("checkout") or {}).get(item)
        dest = payments.link_for(real, lead, slug, item) if real else f"/checkout/{slug}/{item}"
    resp = RedirectResponse(dest, status_code=303)
    if token:
        resp.set_cookie(f"lead_{slug}", token, max_age=AB_COOKIE_DAYS * 86400, samesite="lax", httponly=True)
    return resp


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, background: BackgroundTasks):
    """Stripe -> checkout.session.completed. Configuralo en Stripe > Developers > Webhooks con STRIPE_WEBHOOK_SECRET."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(404)
    body = await request.body()
    if not payments.signature_ok(body, request.headers.get("stripe-signature", ""), STRIPE_WEBHOOK_SECRET):
        raise HTTPException(400, "Firma de Stripe invalida")
    ev = json.loads(body)
    obj = (ev.get("data") or {}).get("object") or {}
    if ev.get("type") != "checkout.session.completed" or obj.get("payment_status") not in ("paid", "no_payment_required"):
        return {"received": True, "ignored": ev.get("type")}
    ref = payments.parse_ref(obj.get("client_reference_id") or "")
    lead = db.get_lead(ref[0]) if ref else None
    landing = db.get_landing(ref[1]) if ref else None
    if not (lead and landing and lead["slug"] == ref[1] and ref[2] in funnel.ITEMS):
        return {"received": True, "ignored": "sin client_reference_id valido"}  # 200: Stripe no debe reintentar
    record_purchase(landing, ref[2], lead, "", background)
    return {"received": True, "purchase": ref[2]}


@app.post("/api/webhooks/hotmart")
async def hotmart_webhook(request: Request, background: BackgroundTasks):
    """Webhook general de Hotmart: la landing sale del sck firmado que el embudo agrega al link de pago."""
    return await hotmart_event(request, background)


@app.post("/api/webhooks/hotmart/{slug}")
async def hotmart_webhook_landing(slug: str, request: Request, background: BackgroundTasks):
    """Webhook de una landing concreta (uno por producto en Hotmart): aunque falte el sck, se sabe de que embudo es."""
    check_slug(slug)
    return await hotmart_event(request, background, slug)


async def hotmart_event(request: Request, background: BackgroundTasks, slug: str = "") -> dict:
    """Postback 2.0 de Hotmart. Todo evento queda registrado en el panel con lo que hizo el sistema."""
    if not HOTMART_HOTTOK:
        raise HTTPException(404)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON invalido")
    token = request.headers.get("x-hotmart-hottok") or str(payload.get("hottok") or "")
    if not secrets.compare_digest(token, HOTMART_HOTTOK):
        raise HTTPException(401, "hottok invalido")
    info = payments.hotmart_info(payload)
    event = info["event"]
    landing = db.get_landing(slug) if slug else None
    lead, item = None, ""

    ref = payments.parse_ref(payments.hotmart_sck(payload), "hotmart")
    if ref and (not slug or ref[1] == slug) and ref[2] in funnel.ITEMS:
        found = db.get_lead(ref[0])
        if found and found["slug"] == ref[1]:
            lead, item, landing = found, ref[2], landing or db.get_landing(ref[1])
    if lead is None and landing and info["email"]:  # sin sck: el comprador por su email entre los leads de esta landing
        lead, item = db.recent_lead(info["email"], landing["slug"], 10 * 365 * 86400), payments.hotmart_item(landing, info)

    if event == "PURCHASE_OUT_OF_SHOPPING_CART" and landing and info["email"] and not info["test"]:
        result = "Carrito abandonado: enviado al CRM"
        background.add_task(webhooks.emit, "cart.abandoned", webhooks.contact(
            "cart.abandoned", landing, lead or {"email": info["email"], "name": info["name"]}, item or payments.hotmart_item(landing, info)))
    elif event not in payments.HOTMART_PAID | payments.HOTMART_REVOKED:
        result = "Registrado (no requiere accion)"
    elif info["test"]:
        result = "Prueba de Hotmart: conexion correcta, sin accion"
    elif not landing:
        result = "Sin landing: usa el webhook de la landing o el link con sck"
    elif event in payments.HOTMART_PAID:
        item = item or payments.hotmart_item(landing, info)
        if lead is None:  # compro directo en Hotmart sin pasar por el formulario: se crea el lead para poder entregarle
            lead = db.add_lead(landing["slug"], "A", info["name"], info["email"], {"utm_source": "hotmart"}) if info["email"] else None
        new = record_purchase(landing, item, lead, "", background)
        result = "Compra registrada: email de acceso en camino" if new else "Compra ya registrada (aviso repetido)"
    else:
        item = item or payments.hotmart_item(landing, info)
        if not db.transaction_paid("hotmart", info["transaction"]):
            # p. ej. tarjeta rechazada: Hotmart cancela una transaccion que nunca aprobo. No se toca ninguna otra compra.
            result = "Pago no completado: no habia compra aprobada de esta transaccion"
        elif lead and db.remove_purchase(lead["id"], landing["slug"], item):
            db.add_event(landing["slug"], f"refund_{item}")
            result = "Acceso cortado y venta descontada"
            background.add_task(webhooks.emit, "purchase.refunded", webhooks.contact(
                "purchase.refunded", landing, lead, item, {"reason": payments.HOTMART_LABELS.get(event, event),
                                                            "transaction": info["transaction"]}))
        else:
            result = "No habia compra registrada que revertir"
    db.add_payment_event(provider="hotmart", slug=(landing or {}).get("slug", ""), event=event, item=item,
                         product=info["product"], transaction_id=info["transaction"], buyer=info["email"],
                         amount=info["amount"], result=result, test=int(info["test"]))
    return {"received": True, "result": result}


@app.get("/api/payments/events", dependencies=[Depends(require_auth)])
async def payment_events(slug: str = ""):
    rows = db.list_payment_events(slug, limit=1000)
    for r in rows:
        r["buyer"] = mask_email(r["buyer"]) if r["buyer"] else ""
        r["label"] = payments.HOTMART_LABELS.get(r["event"], r["event"])
        r["category"] = payments.HOTMART_CATEGORY.get(r["event"], "otro")
    return {"events": rows, "webhook_base": f"{PUBLIC_URL}/api/webhooks/hotmart"}


class ToggleIn(BaseModel):
    active: bool


@app.put("/api/landings/{slug}/seller", dependencies=[Depends(require_auth)])
async def toggle_seller(slug: str, body: ToggleIn):
    """Activa o desactiva la burbuja del agente vendedor en la landing publicada (ManyChat no se ve afectado)."""
    check_slug(slug)
    db.update_landing(slug, seller_chat=int(body.active))
    return {"ok": True, "active": body.active}


class SellerIn(BaseModel):
    messages: list[ChatMsg] = Field(min_length=1, max_length=12)
    preview: bool = False


@app.post("/api/seller/{slug}")
async def seller_chat(slug: str, body: SellerIn, request: Request):
    """Agente vendedor del chat de la landing (publico: con limite por IP y global, porque cada mensaje cuesta tokens)."""
    landing = check_slug(slug)
    if not landing.get("seller_chat"):
        raise HTTPException(403, "El chat esta desactivado en esta landing")
    if body.messages[-1].role != "user":
        raise HTTPException(400, "El ultimo mensaje debe ser del visitante")
    ip = antispam.client_ip(request)
    if not (antispam.limiter.allow(f"seller:{ip}", 30) and antispam.limiter.allow("seller", 600)):
        raise HTTPException(429, "Muchas preguntas seguidas. Espera un momento o deja tu email en el formulario.")
    if not body.preview:
        db.add_event(slug, "chat_message")
    return await seller.reply(landing, page_file(slug).read_text(encoding="utf-8"), [m.model_dump() for m in body.messages])


@app.get("/acceso/{token}", response_class=HTMLResponse)
async def access(token: str):
    """Entrega del producto comprado: redirige al link real o muestra un acceso de ejemplo con la marca del embudo."""
    parts = emails.verify(token)
    if not parts or len(parts) != 3 or parts[2] not in funnel.ITEMS:
        raise HTTPException(404)
    lead_id, slug, item = int(parts[0]), parts[1], parts[2]
    landing = check_slug(slug)
    if item not in db.purchased_items(lead_id, slug):
        raise HTTPException(403, "Este acceso no corresponde a una compra")
    if (landing.get("delivery") or {}).get(item):
        return RedirectResponse(landing["delivery"][item], status_code=303)
    return HTMLResponse(funnel.render_access(emails.product(landing, item), funnel.theme(page_file(slug).read_text(encoding="utf-8"))))


@app.get("/baja/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str):
    parts = emails.verify(token)
    if not parts or len(parts) != 1:
        raise HTTPException(404)
    db.unsubscribe(int(parts[0]))
    return HTMLResponse("""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Baja confirmada</title><meta name="robots" content="noindex"></head><body style="max-width:560px;margin:60px auto;padding:0 20px;font:17px/1.6 system-ui,sans-serif;color:#1b2430">
<h1>Listo, te diste de baja</h1><p>No volveras a recibir emails de esta lista.</p></body></html>""")


# ---------- automatizacion (n8n) ----------
def require_automation_key(request: Request):
    if not AUTOMATION_KEY:
        raise HTTPException(503, "AUTOMATION_KEY no configurada en .env")
    if not secrets.compare_digest(request.headers.get("X-Automation-Key", ""), AUTOMATION_KEY):
        raise HTTPException(401, "Clave de automatizacion invalida")


class ManyChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    mc_id: str = Field(default="", max_length=40)


@app.post("/api/manychat/{slug}/reply", dependencies=[Depends(require_automation_key)])
async def manychat_reply(slug: str, body: ManyChatIn):
    """ManyChat/FunnelChat 'External Request': el bot de Instagram o WhatsApp responde con el mismo agente vendedor."""
    landing = check_slug(slug)
    db.add_event(slug, "chat_message")
    out = await seller.reply(landing, page_file(slug).read_text(encoding="utf-8"), [{"role": "user", "content": body.text}])
    url = f"{PUBLIC_URL}/p/{slug}?utm_source=manychat" + (f"&mc_id={body.mc_id}" if body.mc_id.isdigit() else "")
    return seller.manychat(out["respuesta"], url, out["mostrar_boton"])


@app.get("/api/automation/email", dependencies=[Depends(require_automation_key)])
async def automation_email(lead: int, kind: str, n: int = 0, item: str = "main"):
    """n8n pregunta justo antes de cada envio: la app decide si sigue vigente (compro, se dio de baja) y da el HTML."""
    rec = db.get_lead(lead)
    landing = db.get_landing(rec["slug"]) if rec else None
    if not rec or not landing or not page_file(rec["slug"]).exists():
        return {"send": False, "reason": "lead o embudo inexistente"}
    return emails.build(kind, rec, landing, n, item)


@app.get("/a/{slug}/{name}")
async def asset(slug: str, name: str):
    if not SLUG_RE.fullmatch(slug) or not re.fullmatch(r"(logo\.png|img[1-4]\.jpg)", name):
        raise HTTPException(404)
    f = ASSETS / slug / name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml")
async def sitemap():
    urls = [(f"{PUBLIC_URL}/p/{r['slug']}", r["created"]) for r in db.list_landings() if page_file(r["slug"]).exists()]
    return Response(seo.sitemap(urls), media_type="application/xml")


@app.get("/indexnow.txt", response_class=PlainTextResponse)
async def indexnow_key():
    return integrations.indexnow_key()


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """Resumen en Markdown para buscadores con IA (ChatGPT, Perplexity, Claude): que se vende y donde, sin leer el HTML."""
    lines = ["# Embudos publicados", "", "> Cursos y productos digitales con su pagina de venta.", ""]
    for r in db.list_landings():
        if not page_file(r["slug"]).exists():
            continue
        p = ((db.get_landing(r["slug"]) or {}).get("strategy") or {}).get("producto") or {}
        price = f" ({p['precio']} USD)" if p.get("precio") else ""
        desc = re.sub(r"\s+", " ", p.get("descripcion") or "").strip()
        lines.append(f"- [{p.get('nombre') or r['title']}]({PUBLIC_URL}/p/{r['slug']}){price}" + (f": {desc}" if desc else ""))
    return "\n".join(lines) + "\n"


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return (f"User-agent: *\nAllow: /p/\nAllow: /a/\nDisallow: /api/\nDisallow: /estrategia/\nDisallow: /go/\n"
            f"Disallow: /checkout/\nSitemap: {PUBLIC_URL}/sitemap.xml\n")


@app.get("/privacidad", response_class=HTMLResponse)
async def privacy():
    return """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Politica de privacidad</title><meta name="robots" content="noindex"></head>
<body style="max-width:680px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui,sans-serif;color:#1b2430">
<h1>Politica de privacidad</h1>
<p>Usamos tu nombre y email solo para enviarte la informacion que solicitaste sobre este producto y comunicaciones relacionadas.</p>
<p>No vendemos ni cedemos tus datos a terceros. Pueden procesarse en herramientas de email y automatizacion que actuan por cuenta nuestra.</p>
<p>Para medir la eficacia de nuestros anuncios, registramos tu direccion IP, tu navegador y los identificadores de Meta (cookies _fbp y _fbc) cuando te registras o compras, y los compartimos con Meta (Facebook/Instagram) junto con tu email y nombre cifrados (SHA-256) a traves de su API de conversiones.</p>
<p>Puedes darte de baja en cualquier momento desde el enlace de cada email o pedir la eliminacion de tus datos respondiendo a cualquiera de nuestros mensajes.</p>
</body></html>"""


# ---------- leads ----------
class LeadIn(BaseModel):
    name: str = Field("", max_length=120)
    email: str = Field(min_length=5, max_length=200)
    slug: str = ""
    utm_source: str = Field("", max_length=80)
    utm_medium: str = Field("", max_length=80)
    utm_campaign: str = Field("", max_length=120)
    mc_id: str = Field("", max_length=30)
    fbclid: str = Field("", max_length=500)  # clic de un anuncio de Meta (para la Conversions API)
    la_t: str = Field("", max_length=200)  # token de la visita (lo agrega el script de la pagina)


@app.post("/api/lead")
async def lead(body: LeadIn, request: Request, background: BackgroundTasks):
    landing = check_slug(body.slug)
    email = body.email.strip().lower()
    if not antispam.EMAIL_RE.match(email):
        raise HTTPException(422, "Revisa tu email: parece que tiene un error.")
    if problem := antispam.token_problem(body.la_t, body.slug):
        print(f"[antispam] lead rechazado ({problem}) desde {antispam.client_ip(request)}", flush=True)
        raise HTTPException(400, "No pudimos enviar el formulario. Recarga la pagina e intenta de nuevo.")
    if not antispam.rate_ok(antispam.client_ip(request)):
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos e intenta de nuevo.")

    existing = db.recent_lead(email, body.slug, within=86400)
    if existing:  # ya registrado: no se duplica la secuencia de emails
        resp = JSONResponse({"ok": True, "forwarded_to_n8n": False, "manychat": False, "duplicate": True})
        resp.set_cookie(f"lead_{body.slug}", emails.sign(existing["id"], body.slug), max_age=AB_COOKIE_DAYS * 86400,
                        samesite="lax", httponly=True)
        return resp

    variant = request.cookies.get(f"ab_{body.slug}", "A")
    mc_id = body.mc_id if body.mc_id.isdigit() else request.cookies.get(f"mc_{body.slug}", "")
    fbc = request.cookies.get("_fbc", "") or (f"fb.1.{int(time.time() * 1000)}.{body.fbclid}" if body.fbclid else "")
    meta = {"fbp": request.cookies.get("_fbp", ""), "fbc": fbc, "ip": antispam.client_ip(request),
            "ua": request.headers.get("user-agent", "")}
    rec = db.add_lead(body.slug, variant if variant in ("A", "B") else "A", body.name.strip(), email,
                      body.model_dump(include={"utm_source", "utm_medium", "utm_campaign"}), mc_id, meta)
    s = landing.get("strategy") or {}
    payload = {"type": "lead", **rec, "lead_id": rec["id"], "landing": landing["title"],
               "url": f"{PUBLIC_URL}/p/{body.slug}", "api_base": PUBLIC_URL, "producto": s.get("producto", {}).get("nombre"),
               "schedule": emails.recovery_schedule(rec, s)}
    forwarded = await integrations.send_n8n(payload)
    tagged = await integrations.manychat_tag(mc_id, "lead", {"landing_email": rec["email"]})
    background.add_task(webhooks.emit, "lead.created", webhooks.contact("lead.created", landing, rec))
    event_id = f"lead.{rec['id']}"  # el pixel del navegador usa el mismo: Meta cuenta un solo Lead
    background.add_task(capi.track, "Lead", event_id, rec, f"{PUBLIC_URL}/p/{body.slug}",
                        {"content_name": s.get("producto", {}).get("nombre")})
    resp = JSONResponse({"ok": True, "forwarded_to_n8n": forwarded, "manychat": tagged, "event_id": event_id})
    # vincula las compras de este navegador con el lead
    resp.set_cookie(f"lead_{body.slug}", emails.sign(rec["id"], body.slug), max_age=AB_COOKIE_DAYS * 86400,
                    samesite="lax", httponly=True)
    return resp


@app.get("/api/leads", dependencies=[Depends(require_auth)])
async def list_leads():
    return db.list_leads()


@app.get("/api/leads.csv", dependencies=[Depends(require_auth)])
async def leads_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["fecha", "nombre", "email", "landing", "variante", "utm_source", "utm_medium", "utm_campaign", "slug"])
    for r in db.list_leads():
        w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])), r["name"], r["email"], r["landing"],
                    r["variant"], r["utm_source"], r["utm_medium"], r["utm_campaign"], r["slug"]])
    return Response("﻿" + out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


# ---------- panel ----------
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def home():
    return PANEL

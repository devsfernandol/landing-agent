"""Equipo de agentes que construye un embudo.

  🧠 Estratega ──► ✍️ Copywriter ─────────┐
                  🛒 Arquitecto de embudo ─┼──► 🔍 Auditor tecnico (codigo) ─┐
                  📈 Estratega de contenido┘    🎯 Critico de conversion (LLM)┴──► ✍️ Copywriter rehace ──► revision final

- El Estratega decide; tres especialistas ejecutan en paralelo con su propio prompt y entregable.
- Dos revisores independientes: el Auditor mide con codigo (reproducible) y el Critico lee la landing como el
  cliente ideal. Ninguno es el agente que la escribio.
- Sus observaciones se combinan en una sola devolucion al Copywriter; la nueva version solo se queda si no empeora
  en ninguna de las dos revisiones.
Cada paso queda en una traza con el agente que lo hizo; el panel la muestra en vivo.
"""
import asyncio
import json
import time
import uuid

from bs4 import BeautifulSoup
from fastapi import HTTPException

from . import db, integrations
from .config import PUBLIC_URL
from .llm import call_llm, call_llm_json, new_usage
from .pages import clean_placeholders, extract_html, finalize, page_file
from .prompts import (ASSETS_SYSTEM, CRITIC_SYSTEM, FUNNEL_SYSTEM, LANDING_SYSTEM, META_FIX_SYSTEM, REFINE_SYSTEM,
                      STRATEGY_SYSTEM, TONES, landing_strategy_brief, strategy_prompt)
from .seo import audit, h1_text

TARGET_SEO = 90
CRITIC_MIN = 7
META_CHECKS = {"title", "title_kw", "desc", "desc_kw"}
BODY_FIXES = {"h1_kw", "h2", "kw_first", "words", "cta", "price", "guarantee", "faq", "form"}

AGENTS = {
    "estratega": ("🧠", "Estratega", "Avatar, consciencia, framework y keywords"),
    "copywriter": ("✍️", "Copywriter", "Escribe la landing con el framework elegido"),
    "arquitecto": ("🛒", "Arquitecto de embudo", "Upsell, downsell, gracias y guiones de video"),
    "contenido": ("📈", "Estratega de contenido", "Cluster SEO, lead magnets, emails y anuncios"),
    "auditor": ("🔍", "Auditor tecnico", "SEO y conversion medidos con codigo"),
    "critico": ("🎯", "Critico de conversion", "Lee la landing como el cliente ideal"),
    "optimizador": ("🤖", "Optimizador", "Analiza metricas y mejora el embudo con herramientas"),
}


class Job:
    def __init__(self, kind: str, slug: str, agents: tuple[str, ...] = ()):
        self.id, self.kind, self.slug = uuid.uuid4().hex[:10], kind, slug
        self.status, self.result, self.error = "running", None, None
        self.trace: list[dict] = []
        self.agents = {a: {"status": "espera", "note": ""} for a in agents}
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        self.t0 = time.time()

    def log(self, kind: str, text: str, agent: str = "") -> None:
        self.trace.append({"t": round(time.time() - self.t0, 1), "kind": kind, "text": text, "agent": agent})
        if agent in self.agents and kind != "error":
            self.agents[agent]["note"] = text
        print(f"[agente {self.id}] {agent or '-'} {kind}: {text}", flush=True)

    def set(self, agent: str, status: str) -> None:
        """status: espera | trabajando | revisando | rehaciendo | listo | error"""
        if agent in self.agents:
            self.agents[agent]["status"] = status

    def public(self) -> dict:
        team = [{"id": a, "emoji": AGENTS[a][0], "name": AGENTS[a][1], "role": AGENTS[a][2], **st} for a, st in self.agents.items()]
        return {"id": self.id, "kind": self.kind, "slug": self.slug, "status": self.status, "agents": team,
                "trace": self.trace, "result": self.result, "error": self.error, "usage": self.usage,
                "elapsed": round(time.time() - self.t0, 1)}


JOBS: dict[str, Job] = {}


async def ping_indexnow(job: "Job", slug: str) -> None:
    """Aviso a buscadores al publicar: no retrasa el final del trabajo y, si falla, no pasa nada."""
    if await integrations.indexnow([f"{PUBLIC_URL}/p/{slug}"]):
        job.log("check", "IndexNow: Bing y Yandex ya saben que la landing existe (indexacion en minutos, no semanas)")
        db.update_landing(slug, trace=job.trace)


def start(kind: str, slug: str, coro_factory, agents: tuple[str, ...] = ()) -> Job:
    job = Job(kind, slug, agents)
    JOBS[job.id] = job
    for old in list(JOBS)[:-50]:
        JOBS.pop(old, None)

    async def runner():
        job.usage = new_usage()  # las llamadas al LLM de este trabajo suman aqui (tambien las paralelas)
        try:
            job.result = await coro_factory(job)
            job.status = "done"
        except HTTPException as ex:
            job.status, job.error = "error", str(ex.detail)
            job.log("error", str(ex.detail))
        except Exception as ex:  # el panel debe ver el error, no un spinner eterno
            job.status, job.error = "error", f"{type(ex).__name__}: {ex}"
            job.log("error", job.error)
        finally:
            for st in job.agents.values():
                if st["status"] in ("trabajando", "revisando", "rehaciendo"):
                    st["status"] = "error" if job.status == "error" else "listo"
            if job.kind in ("generate", "optimize") and db.get_landing(slug):
                db.update_landing(slug, trace=job.trace, usage={**job.usage, "seconds": round(time.time() - job.t0, 1)})
                if job.status == "done":
                    asyncio.get_running_loop().create_task(ping_indexnow(job, slug))

    asyncio.get_running_loop().create_task(runner())
    return job


# ---------- agentes ----------
async def estratega(brief: str, framework: str, tone: str, job: Job) -> dict:
    job.set("estratega", "trabajando")
    job.log("think", "Analizando el brief: quien compra, que le duele y cuanto sabe del problema", "estratega")
    s = await call_llm_json(strategy_prompt(brief, framework, tone), STRATEGY_SYSTEM, max_tokens=3500)
    a, seo = s.get("avatar", {}), s.get("seo", {})
    job.log("decision", f"Avatar: {a.get('nombre', '?')} · consciencia: {a.get('nivel_consciencia', '?')}", "estratega")
    job.log("decision", f"Framework {s.get('framework', '?')}: {s.get('por_que_framework', '')}", "estratega")
    job.log("decision", f"Keyword principal: «{seo.get('keyword_principal', '')}» (intencion {seo.get('intencion', '?')})", "estratega")
    job.log("decision", f"Test A/B: «{s.get('titular_a', '')}» vs «{s.get('titular_b', '')}»", "estratega")
    job.set("estratega", "listo")
    return s


def _strategy_core(s: dict) -> str:
    keep = {k: s.get(k) for k in ("producto", "avatar", "framework", "big_idea", "seo")}
    return "ESTRATEGIA:\n" + json.dumps(keep, ensure_ascii=False)


async def contenido(s: dict, job: Job) -> dict:
    """Si un entregable falla no se cae el embudo: la traza lo explica y el informe lo muestra vacio."""
    job.set("contenido", "trabajando")
    try:
        a = await call_llm_json(_strategy_core(s), ASSETS_SYSTEM, max_tokens=4000)
        job.log("tool", f"{len(a.get('cluster', {}).get('articulos', []))} articulos SEO, {len(a.get('emails', []))} emails, "
                        f"{len(a.get('lead_magnets', []))} lead magnets y {len(a.get('anuncios', []))} hooks de anuncios", "contenido")
        job.set("contenido", "listo")
        return a
    except HTTPException as ex:
        job.log("warn", f"Plan de contenido no generado: {ex.detail}", "contenido")
        job.set("contenido", "error")
        return {}


async def arquitecto(s: dict, job: Job) -> dict:
    job.set("arquitecto", "trabajando")
    try:
        f = await call_llm_json(_strategy_core(s), FUNNEL_SYSTEM, max_tokens=4000)
        u, d = f.get("upsell", {}), f.get("downsell", {})
        job.log("tool", f"Upsell «{u.get('nombre', '?')}» ({u.get('precio', '?')}) · downsell «{d.get('nombre', '?')}» ({d.get('precio', '?')})", "arquitecto")
        job.log("tool", f"Guiones: VSL de {len(f.get('vsl', {}).get('escenas', []))} escenas y {len(f.get('reels', []))} reels", "arquitecto")
        job.set("arquitecto", "listo")
        return f
    except HTTPException as ex:
        job.log("warn", f"Embudo no generado: {ex.detail}", "arquitecto")
        job.set("arquitecto", "error")
        return {}


async def copywriter(prompt: str, job: Job) -> str:
    job.set("copywriter", "trabajando")
    raw = await call_llm(prompt, LANDING_SYSTEM)
    html, complete = extract_html(raw)
    if not complete:
        job.log("warn", "La landing salio cortada: reintentando con una version mas compacta", "copywriter")
        raw = await call_llm(prompt + "\n\nIMPORTANTE: tu respuesta anterior se corto. Hazla MAS CORTA: CSS minimo, "
                                      "testimonios de una linea, sin secciones extra. Debe terminar con </html>.", LANDING_SYSTEM)
        html, complete = extract_html(raw)
    job.log("tool", f"Landing escrita: {len(html.encode()) / 1024:.1f} KB" + ("" if complete else " (reparada)"), "copywriter")
    job.set("copywriter", "listo")
    return html


def visible_copy(html: str, limit: int = 12000) -> str:
    """La landing como la lee una persona: titulos marcados y texto, sin codigo."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "head"]):
        t.decompose()
    lines = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "button", "a", "label", "blockquote", "figcaption"]):
        text = el.get_text(" ", strip=True)
        if text and not el.find_parent(["p", "li", "button", "a"]):
            tag = el.name.upper() if el.name in ("h1", "h2", "h3") else ("CTA" if el.name in ("button", "a") else "")
            lines.append(f"{tag}: {text}" if tag else text)
    return "\n".join(dict.fromkeys(lines))[:limit]


def critic_avg(c: dict | None) -> float:
    notes = [x.get("nota", 0) for x in (c or {}).get("secciones", []) if isinstance(x.get("nota"), (int, float))]
    return round(sum(notes) / len(notes), 1) if notes else 0.0


async def critico(landing: dict, html: str, job: Job, label: str = "Leyendo la landing como", previous: dict | None = None) -> dict:
    job.set("critico", "revisando")
    s = landing.get("strategy") or {}
    a = s.get("avatar", {})
    job.log("think", f"{label} {a.get('nombre', 'el cliente ideal')} ({a.get('nivel_consciencia', '')})", "critico")
    brief = json.dumps({"avatar": a, "producto": s.get("producto"), "framework": s.get("framework"),
                        "faq_prevista": [q.get("pregunta") for q in s.get("faq", [])]}, ensure_ascii=False)
    before = ""
    if previous:  # misma vara de medir en la segunda lectura: reduce la variacion entre evaluaciones
        keep = [{k: x.get(k) for k in ("seccion", "nota", "mejora")} for x in previous.get("secciones", [])]
        before = "\n\nEVALUACION ANTERIOR:\n" + json.dumps(keep, ensure_ascii=False)
    try:
        c = await call_llm_json(f"CLIENTE IDEAL Y OFERTA:\n{brief}{before}\n\nLANDING (texto visible):\n{visible_copy(html)}",
                                CRITIC_SYSTEM, max_tokens=1800)
    except HTTPException as ex:
        job.log("warn", f"No se pudo evaluar: {ex.detail}", "critico")
        job.set("critico", "error")
        return {}
    c["promedio"] = critic_avg(c)
    weak = [x for x in c.get("secciones", []) if x.get("nota", 10) < CRITIC_MIN]
    job.log("check", f"Nota media {c['promedio']}/10 · " + (
        "aprueba todas las secciones" if not weak else
        "rechaza: " + ", ".join(f"{x['seccion']} ({x['nota']})" for x in weak)), "critico")
    if c.get("objecion_sin_responder"):
        job.log("decision", f"Objecion sin responder: {c['objecion_sin_responder']}", "critico")
    job.set("critico", "listo")
    return c


def auditor(landing: dict, html: str, job: Job) -> tuple[dict, dict]:
    job.set("auditor", "revisando")
    rep, conv = finalize(landing, html)
    fails = [c["label"].lower() for c in rep["checks"] + conv["checks"] if not c["ok"]]
    job.log("check", f"SEO {rep['score']}/100 · conversion {conv['passed']}/{conv['total']}"
                     + (f". Fallos: {', '.join(fails)}" if fails else ". Sin fallos"), "auditor")
    job.set("auditor", "listo")
    return rep, conv


def _audit_rules(failed: list[dict], s: dict) -> list[str]:
    seo, p = s.get("seo", {}), s.get("producto", {})
    kw = seo.get("keyword_principal", "")
    faq = "; ".join(q.get("pregunta", "") for q in s.get("faq", []))
    rules = {
        "h1_kw": f"Reescribe el <h1> para que incluya «{kw}» de forma natural, manteniendo la promesa.",
        "h2": f"Asegura al menos 3 <h2> descriptivos que usen estas keywords: {', '.join(seo.get('keywords_secundarias', []))}.",
        "kw_first": f"Incluye «{kw}» en el subtitular del hero.",
        "words": "Amplia el copy de solucion y beneficios hasta superar 350 palabras visibles, concreto y sin relleno.",
        "cta": "Agrega llamadas a la accion (enlaces al formulario) al final de beneficios y de la oferta hasta tener al menos 3.",
        "price": f"Muestra el precio {p.get('precio')} {p.get('moneda', '')} en la seccion de oferta.",
        "guarantee": "Muestra la garantia indicada en el brief dentro de la seccion de oferta.",
        "faq": f"La seccion FAQ debe contener exactamente estas preguntas: {faq}.",
        "form": "El formulario debe tener input de email y el fetch a /api/lead descrito, con el slug que ya figura en la pagina.",
    }
    return [rules[c["id"]] for c in failed if c["id"] in rules]


async def revision(landing: dict, rep: dict, conv: dict, crit: dict, job: Job) -> tuple[dict, dict, dict]:
    """Una ronda: Auditor y Critico devuelven el trabajo al Copywriter; la version nueva solo queda si no empeora."""
    slug, s = landing["slug"], landing["strategy"]
    meta_failed = [c for c in rep["checks"] if not c["ok"] and c["id"] in META_CHECKS]
    body_failed = [c for c in rep["checks"] + conv["checks"] if not c["ok"] and c["id"] not in META_CHECKS]
    weak = [x for x in crit.get("secciones", []) if x.get("nota", 10) < CRITIC_MIN]
    rules = _audit_rules(body_failed, s) + [f"[{x['seccion']}] {x['mejora']}" for x in weak if x.get("mejora")]
    if crit.get("objecion_sin_responder") and weak:
        rules.append(f"Responde de forma explicita esta objecion: {crit['objecion_sin_responder']}")
    manual = [c["label"].lower() for c in body_failed if c["id"] not in BODY_FIXES]
    if manual:
        job.log("warn", "Requiere datos del cliente: " + ", ".join(manual), "auditor")
    if not meta_failed and not rules:
        job.log("done", "Ambos revisores aprueban: no hace falta reescribir", "critico" if crit else "auditor")
        return rep, conv, crit

    job.log("handoff", f"Auditor y Critico devuelven la landing al Copywriter con {len(rules) + len(meta_failed)} correcciones", "critico")
    job.set("copywriter", "rehaciendo")
    s2 = json.loads(json.dumps(s))  # copia: si empeora se descarta
    before_html = page_file(slug).read_text(encoding="utf-8")
    tasks = []
    if meta_failed:
        tasks.append(call_llm_json(
            f"Keyword principal: {s2['seo'].get('keyword_principal')}\nProducto: {s2.get('producto', {}).get('nombre')}\n"
            f"Title actual: {s2['seo'].get('meta_title')}\nDescription actual: {s2['seo'].get('meta_description')}",
            META_FIX_SYSTEM, max_tokens=400))
    if rules:
        tasks.append(call_llm("INSTRUCCION: aplica SOLO estas correcciones de los revisores y conserva todo lo demas:\n- "
                              + "\n- ".join(rules) + f"\n\nHTML ACTUAL:\n{before_html}", REFINE_SYSTEM))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    html = before_html
    if meta_failed:
        meta = results.pop(0)
        if isinstance(meta, dict) and meta.get("meta_title"):
            s2["seo"].update({k: meta[k] for k in ("meta_title", "meta_description") if meta.get(k)})
    if rules:
        out = results.pop(0)
        fixed, complete = extract_html(out) if isinstance(out, str) else ("", False)
        if complete:
            html = fixed
            job.log("tool", "Nueva version escrita con las correcciones", "copywriter")
        else:
            job.log("warn", "La reescritura no se completo: se conserva el cuerpo anterior", "copywriter")
    job.set("copywriter", "listo")

    db.update_landing(slug, strategy=s2)
    candidate = db.get_landing(slug)
    new_rep, new_conv = auditor(candidate, html, job)
    new_crit = await critico(candidate, html, job, "Releyendo la nueva version como", previous=crit) if crit and rules else crit
    worse_audit = (new_rep["score"], new_conv["passed"]) < (rep["score"], conv["passed"])
    worse_copy = bool(crit) and critic_avg(new_crit) < critic_avg(crit) - 0.3
    if worse_audit or worse_copy:
        job.log("warn", "La nueva version no supera a la anterior en las dos revisiones: se descarta", "auditor")
        db.update_landing(slug, strategy=s)
        rep, conv = finalize(db.get_landing(slug), before_html)
        return rep, conv, crit
    job.log("done", f"Version nueva aceptada · SEO {rep['score']} → {new_rep['score']} · conversion {conv['passed']} → {new_conv['passed']}/{new_conv['total']}"
                    + (f" · critico {critic_avg(crit)} → {critic_avg(new_crit)}" if crit else ""), "auditor")
    still = [x["seccion"] for x in new_crit.get("secciones", []) if x.get("nota", 10) < CRITIC_MIN] if new_crit else []
    if still:
        job.log("warn", "Para el siguiente ciclo (o el optimizador): " + ", ".join(still), "critico")
    new_crit = {**new_crit, "inicial": critic_avg(crit)} if crit else new_crit
    return new_rep, new_conv, new_crit


# ---------- tareas completas ----------
TEAM = tuple(a for a in AGENTS if a != "optimizador")


async def generate(job: Job, p: dict) -> dict:
    s = await estratega(p["prompt"], p["framework"], p["tono"], job)
    full_prompt = p["prompt"] + p["brand"] + (f"\n{TONES[p['tono']]}" if p["tono"] else "") + landing_strategy_brief(s)
    job.log("handoff", "Estrategia lista: Copywriter, Arquitecto y Estratega de contenido trabajan en paralelo", "estratega")
    html, assets, funnel = await asyncio.gather(copywriter(full_prompt, job), contenido(s, job), arquitecto(s, job))
    s["assets"] = assets
    slug = job.slug
    html = clean_placeholders(html, slug, p["has_logo"], p["n_imgs"])
    db.create_landing(slug, title=p["prompt"][:70], brief=p["prompt"], created=int(time.time()), logo=int(p["has_logo"]),
                      imgs=p["n_imgs"], colors=p["colors"], framework=s.get("framework", ""), strategy=s,
                      funnel=funnel, checkout=p["checkout"])
    landing = db.get_landing(slug)
    rep, conv = auditor(landing, html, job)
    crit = await critico(db.get_landing(slug), page_file(slug).read_text(encoding="utf-8"), job)
    rep, conv, crit = await revision(db.get_landing(slug), rep, conv, crit, job)
    db.update_landing(slug, critic=crit)
    url = f"{PUBLIC_URL}/p/{slug}"
    job.log("done", f"Embudo publicado: {url}")
    return {"slug": slug, "url": url, "seo_score": rep["score"], "conv": f"{conv['passed']}/{conv['total']}",
            "critic": critic_avg(crit), "framework": s.get("framework"), "keyword": s.get("seo", {}).get("keyword_principal"),
            "seconds": round(time.time() - job.t0, 1)}


async def optimize(job: Job, landing: dict) -> dict:
    """Para landings anteriores al equipo: estrategia, embudo, revision y correccion sobre la pagina existente."""
    slug = landing["slug"]
    html = page_file(slug).read_text(encoding="utf-8")
    before = audit(html)["score"]
    job.log("check", f"SEO actual: {before}/100", "auditor")
    s = await estratega(landing["brief"] or landing["title"], "", "", job)
    h1 = h1_text(html)
    if h1:  # el H1 publicado es la variante A; el titular nuevo pasa a ser el retador B
        s["titular_b"], s["titular_a"] = s.get("titular_a", ""), h1
        job.log("decision", "El H1 publicado queda como A; el titular de la estrategia pasa a B", "estratega")
    assets, funnel = await asyncio.gather(contenido(s, job), arquitecto(s, job))
    s["assets"] = assets
    db.update_landing(slug, strategy=s, funnel=funnel, framework=s.get("framework", ""))
    rep, conv = auditor(db.get_landing(slug), html, job)
    crit = await critico(db.get_landing(slug), page_file(slug).read_text(encoding="utf-8"), job)
    rep, conv, crit = await revision(db.get_landing(slug), rep, conv, crit, job)
    db.update_landing(slug, critic=crit)
    job.log("done", f"SEO {before} → {rep['score']}/100")
    return {"slug": slug, "before": before, "after": rep["score"], "conv": f"{conv['passed']}/{conv['total']}",
            "critic": critic_avg(crit)}

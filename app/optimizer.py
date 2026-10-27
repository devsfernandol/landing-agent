"""Agente optimizador: conversa sobre un embudo y decide por si mismo que herramientas usar (tool calling).

A diferencia del equipo que construye el embudo (flujo fijo), aqui el modelo elige los pasos: consulta metricas,
lee la landing, pide una evaluacion al critico y, si el usuario lo aprueba, modifica la landing o las ofertas.
Cada llamada a herramienta queda en la traza del trabajo, que el panel muestra en vivo.
"""
import json

from fastapi import HTTPException

from . import db, funnel
from .agent import Job, critic_avg, critico, visible_copy
from .llm import call_llm, call_llm_json, chat_with_tools
from .pages import check_slug, extract_html, finalize, page_file
from .prompts import OFFER_SYSTEM, OPTIMIZER_SYSTEM, REFINE_SYSTEM

TOOLS = [
    {"name": "ver_metricas", "description": "Metricas del embudo: visitas, leads, checkout, compras, upsell, downsell, ingresos, "
     "ticket medio, test A/B, canales, puntajes SEO/conversion y nota del critico.", "parameters": {"type": "object", "properties": {}}},
    {"name": "ver_embudo", "description": "Producto, precio, avatar, framework, titulares A/B y las ofertas de upsell y downsell.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "leer_landing", "description": "Texto visible de la landing publicada (titulos, parrafos y CTA).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "criticar_landing", "description": "Pide al agente critico una evaluacion fresca de la landing (notas por seccion y objecion sin responder).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "editar_landing", "description": "Modifica la landing publicada segun una instruccion concreta. Guarda la version anterior. "
     "Solo con aprobacion del usuario.", "parameters": {"type": "object", "properties": {
         "instruccion": {"type": "string", "description": "Cambio concreto a aplicar"}}, "required": ["instruccion"]}},
    {"name": "deshacer_edicion_landing", "description": "Restaura la version de la landing anterior a la ultima edicion.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "reescribir_oferta", "description": "Reescribe el upsell o el downsell (angulo, beneficios, precio). Solo con aprobacion del usuario.",
     "parameters": {"type": "object", "properties": {
         "paso": {"type": "string", "enum": ["upsell", "downsell"]},
         "instruccion": {"type": "string"},
         "precio": {"type": "number", "description": "Nuevo precio en USD (opcional)"}}, "required": ["paso", "instruccion"]}},
    {"name": "activar_test_ab", "description": "Activa o desactiva el test A/B del titular de la landing.",
     "parameters": {"type": "object", "properties": {"activo": {"type": "boolean"}}, "required": ["activo"]}},
]
WRITES = {"editar_landing", "deshacer_edicion_landing", "reescribir_oferta", "activar_test_ab"}


async def apply_instruction(landing: dict, instruction: str) -> tuple[dict, dict]:
    """Edita la landing con una instruccion, guardando la version anterior para poder deshacer."""
    f = page_file(landing["slug"])
    current = f.read_text(encoding="utf-8")
    raw = await call_llm(f"INSTRUCCION: {instruction}\n\nHTML ACTUAL:\n{current}", REFINE_SYSTEM)
    html, complete = extract_html(raw)
    if not complete:
        raise HTTPException(502, "El ajuste salio cortado y no se aplico. Pide un cambio mas pequeno.")
    f.with_suffix(".prev.html").write_text(current, encoding="utf-8")
    return finalize(landing, html)


def _metrics(slug: str) -> dict:
    landing = check_slug(slug)
    v = db.variant_stats(slug)
    s = landing.get("strategy") or {}
    m = funnel.metrics(v, db.event_counts(slug), landing.get("funnel"), s.get("producto", {}))
    crit = landing.get("critic") or {}
    return {"pasos": m["steps"], "ingresos_usd": m["revenue"], "ticket_medio_usd": m["aov"], "variantes": v,
            "test_ab": {"activo": landing["ab_active"], **(funnel.ab_summary(v) if s.get("titular_b") else {})},
            "leads_por_canal": db.channel_counts(slug), "seo": landing.get("seo_score"),
            "conversion_checks": f"{(landing.get('conv_report') or {}).get('passed')}/{(landing.get('conv_report') or {}).get('total')}",
            "critico": {"promedio": crit.get("promedio"), "debiles": [x for x in crit.get("secciones", []) if x.get("nota", 10) < 7]}}


async def _rewrite_offer(slug: str, paso: str, instruccion: str, precio=None) -> dict:
    landing = check_slug(slug)
    f = landing.get("funnel") or {}
    if paso not in ("upsell", "downsell") or not f.get(paso):
        return {"error": "Este embudo no tiene esa oferta"}
    s = landing.get("strategy") or {}
    ctx = {"producto_principal": s.get("producto"), "avatar": s.get("avatar"), "oferta_actual": f[paso],
           "instruccion": instruccion, "precio_nuevo": precio}
    new = await call_llm_json(json.dumps(ctx, ensure_ascii=False), OFFER_SYSTEM, max_tokens=1200)
    if precio:
        new["precio"] = precio
    f[f"{paso}_anterior"], f[paso] = f[paso], {**f[paso], **new}
    db.update_landing(slug, funnel=f)
    return {"ok": True, "oferta": f[paso], "anterior": {k: f[f"{paso}_anterior"].get(k) for k in ("nombre", "precio", "titular")}}


async def chat(job: Job, slug: str, messages: list[dict]) -> dict:
    job.set("optimizador", "trabajando")
    job.log("think", "Leyendo la conversacion y decidiendo que datos necesito", "optimizador")

    async def run_tool(name: str, args: dict) -> str:
        landing = check_slug(slug)
        shown = ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items())
        job.log("tool", f"{name}({shown})", "optimizador")
        try:
            if name == "ver_metricas":
                out = _metrics(slug)
            elif name == "ver_embudo":
                s, f = landing.get("strategy") or {}, landing.get("funnel") or {}
                keep = ("nombre", "precio", "precio_ancla", "titular", "bullets", "por_que_ahora")
                out = {"producto": s.get("producto"), "avatar": s.get("avatar"), "framework": s.get("framework"),
                       "titular_a": s.get("titular_a"), "titular_b": s.get("titular_b"),
                       "upsell": {k: f.get("upsell", {}).get(k) for k in keep},
                       "downsell": {k: f.get("downsell", {}).get(k) for k in keep}}
            elif name == "leer_landing":
                out = {"landing": visible_copy(page_file(slug).read_text(encoding="utf-8"), 7000)}
            elif name == "criticar_landing":
                c = await critico(landing, page_file(slug).read_text(encoding="utf-8"), job)
                if c:
                    db.update_landing(slug, critic=c)
                out = c or {"error": "el critico no pudo evaluar"}
            elif name == "editar_landing":
                job.set("optimizador", "rehaciendo")
                rep, conv = await apply_instruction(landing, args.get("instruccion", ""))
                out = {"ok": True, "seo": rep["score"], "conversion": f"{conv['passed']}/{conv['total']}",
                       "nota": "Se guardo la version anterior (deshacer_edicion_landing)"}
            elif name == "deshacer_edicion_landing":
                prev = page_file(slug).with_suffix(".prev.html")
                if not prev.exists():
                    out = {"error": "No hay version anterior"}
                else:
                    html = prev.read_text(encoding="utf-8")
                    prev.unlink()
                    rep, _ = finalize(landing, html)
                    out = {"ok": True, "seo": rep["score"]}
            elif name == "reescribir_oferta":
                job.set("optimizador", "rehaciendo")
                out = await _rewrite_offer(slug, args.get("paso", ""), args.get("instruccion", ""), args.get("precio"))
            elif name == "activar_test_ab":
                if args.get("activo") and not page_file(slug, "B").exists():
                    out = {"error": "No hay variante B para esta landing"}
                else:
                    db.update_landing(slug, ab_active=int(bool(args.get("activo"))))
                    out = {"ok": True, "activo": bool(args.get("activo"))}
            else:
                out = {"error": f"herramienta desconocida: {name}"}
        except HTTPException as ex:
            out = {"error": str(ex.detail)}
        finally:
            job.set("optimizador", "trabajando")
        if name in WRITES and "error" not in out:
            job.log("done", f"Cambio aplicado con {name}", "optimizador")
        elif "error" in out:
            job.log("warn", f"{name}: {out['error']}", "optimizador")
        return json.dumps(out, ensure_ascii=False, default=str)[:12000]

    reply = await chat_with_tools(OPTIMIZER_SYSTEM, messages, TOOLS, run_tool)
    job.set("optimizador", "listo")
    used = [t["text"].split("(")[0] for t in job.trace if t["kind"] == "tool"]
    changed = any(u in WRITES for u in used)
    job.log("done", "Respuesta lista" + (f" · herramientas: {', '.join(dict.fromkeys(used))}" if used else ""), "optimizador")
    return {"reply": reply, "changed": changed, "critic": critic_avg((db.get_landing(slug) or {}).get("critic"))}

"""Informe por embudo para el cliente: estrategia, embudo y metricas, SEO, A/B, contenido, video, emails y traza del agente."""
from datetime import datetime
from html import escape as e

from .agent import AGENTS
from .config import PUBLIC_URL


def _li(items) -> str:
    return "".join(f"<li>{e(str(x))}</li>" for x in items or [])


def _check(c: dict) -> str:
    mark = '<span class="ok">✓</span>' if c["ok"] else '<span class="ko">✕</span>'
    detail = f' <small>{e(c["detail"])}</small>' if c.get("detail") else ""
    return f"<li>{mark} {e(c['label'])}{detail}</li>"


def _money(v) -> str:
    return f"{v:,.0f} USD".replace(",", ".") if isinstance(v, (int, float)) else "—"


def _funnel_section(landing: dict) -> str:
    f, m, slug = landing.get("funnel") or {}, landing["metrics"], landing["slug"]
    if not f.get("upsell"):
        return ""
    u, d = f["upsell"], f.get("downsell", {})
    top = max(m["steps"][0]["n"], 1)

    def bar(s: dict) -> str:
        rate = f" <small>{s['rate']}% {e(s.get('of', ''))}</small>" if s["rate"] is not None else ""
        return (f"<div class='fstep'><div class='flabel'><b>{s['n']}</b> {e(s['label'])}{rate}</div>"
                f"<div class='fbar'><i style='width:{max(2, 100 * s['n'] / top):.0f}%'></i></div></div>")

    bars = "".join(bar(s) for s in m["steps"])
    checkout = landing.get("checkout") or {}
    links = "".join(
        f"<tr><td>{label}</td><td>{e(checkout.get(k) or 'Checkout de demostracion')}</td><td><code>{e(PUBLIC_URL)}/go/{slug}/paid-{k}</code></td></tr>"
        for k, label in (("main", "Producto principal"), ("upsell", "Upsell"), ("downsell", "Downsell")))
    return f"""<section><h2>Embudo de venta</h2>
<div class="flow"><a href="/p/{slug}?preview=1" target="_blank">Landing</a><span>→</span><a href="/checkout/{slug}/main" target="_blank">Checkout</a><span>→</span>
<a href="/p/{slug}/upsell?preview=1" target="_blank">Upsell</a><span>→ si rechaza →</span><a href="/p/{slug}/downsell?preview=1" target="_blank">Downsell</a><span>→</span>
<a href="/p/{slug}/gracias?preview=1" target="_blank">Gracias</a></div>
<div class="grid3">
 <div class="card"><span class="tag">Principal</span><b>{e(landing['strategy'].get('producto', {}).get('nombre', ''))}</b><p class="pr">{_money(m['prices']['main'])}</p></div>
 <div class="card"><span class="tag">Upsell (OTO)</span><b>{e(u.get('nombre', ''))}</b><p class="pr">{_money(u.get('precio'))}</p><ul>{_li(u.get('bullets'))}</ul></div>
 <div class="card"><span class="tag">Downsell</span><b>{e(d.get('nombre', ''))}</b><p class="pr">{_money(d.get('precio'))}</p><ul>{_li(d.get('bullets'))}</ul></div>
</div>
<h3>Conversion por paso</h3>{bars}
<p>Ingresos: <b>{_money(m['revenue'])}</b> · Ticket medio (AOV): <b>{_money(m['aov'])}</b></p>
<h3>Links de pago</h3><div class="tablewrap"><table><thead><tr><th>Producto</th><th>Checkout</th><th>URL de exito a configurar en Stripe/Hotmart</th></tr></thead><tbody>{links}</tbody></table></div>
</section>"""


def _video_section(f: dict) -> str:
    vsl, reels = f.get("vsl") or {}, f.get("reels") or []
    if not vsl and not reels:
        return ""
    rows = "".join(f"<tr><td>{e(x.get('tiempo', ''))}</td><td><span class='tag'>{e(x.get('bloque', ''))}</span></td>"
                   f"<td>{e(x.get('visual', ''))}</td><td>{e(x.get('voz', ''))}</td></tr>" for x in vsl.get("escenas", []))
    rl = "".join(f"<div class='card'><span class='tag'>Reel · {e(r.get('duracion', ''))}</span><b>“{e(r.get('hook', ''))}”</b>"
                 f"<ol>{_li(r.get('escenas'))}</ol><p><b>CTA:</b> {e(r.get('cta', ''))}</p></div>" for r in reels)
    return f"""<section><h2>Guiones de video</h2>
<p class="muted">Listos para produccion o para enviarse a una API de video (avatar IA o texto a video). VSL de {e(vsl.get('duracion', ''))}.</p>
<div class="tablewrap"><table><thead><tr><th>Tiempo</th><th>Bloque</th><th>Visual</th><th>Voz</th></tr></thead><tbody>{rows}</tbody></table></div>
<h3>Reels para trafico</h3><div class="grid3">{rl}</div></section>"""


def _critic_section(c: dict | None) -> str:
    if not c or not c.get("secciones"):
        return ""
    def bar(n):
        tone = "#17803d" if n >= 8 else "#d18a00" if n >= 7 else "#c02626"
        return f"<div class='fbar' style='width:120px;display:inline-block;vertical-align:middle'><i style='width:{n * 10}%;background:{tone}'></i></div>"
    rows = "".join(f"<tr><td><b>{e(x.get('seccion', ''))}</b></td><td style='white-space:nowrap'>{bar(x.get('nota', 0))} {x.get('nota', '')}/10</td>"
                   f"<td>{e(x.get('motivo', ''))}</td></tr>" for x in c["secciones"])
    evolution = f" (la primera version obtuvo {c['inicial']})" if c.get("inicial") is not None else ""
    return f"""<section><h2>Evaluacion del critico de conversion: {c.get('promedio', '')}/10{evolution}</h2>
<p class="muted">Un agente independiente lee la landing como el cliente ideal y la devuelve al copywriter si una seccion baja de 7.</p>
<p class="big">{e(c.get('resumen', ''))}</p>
<div class="tablewrap"><table><thead><tr><th>Seccion</th><th>Nota</th><th>Por que</th></tr></thead><tbody>{rows}</tbody></table></div></section>"""


def _trace_section(trace) -> str:
    if not trace:
        return ""
    who = lambda a: f"{AGENTS[a][0]} {AGENTS[a][1]} " if a in AGENTS else ""
    items = "".join(f"<li class='k-{e(x.get('kind', ''))}'><code>{x.get('t', 0):>5}s</code> {e(who(x.get('agent', '')))}<span class='tag'>{e(x.get('kind', ''))}</span> {e(x.get('text', ''))}</li>"
                    for x in trace)
    return f"<section><h2>Traza del equipo de agentes</h2><p class='muted'>Cada decision, entrega y devolucion de la ultima ejecucion.</p><ul class='trace'>{items}</ul></section>"


def render(landing: dict) -> str:
    slug = landing["slug"]
    url = f"{PUBLIC_URL}/p/{slug}"
    s = landing.get("strategy") or {}
    p, a, seo, assets = s.get("producto", {}), s.get("avatar", {}), s.get("seo", {}), s.get("assets", {})
    rep = landing.get("seo_report") or {"score": 0, "checks": []}
    conv = landing.get("conv_report") or {"passed": 0, "total": 0, "checks": []}
    m, v, ab = landing["metrics"], landing["variants"], landing.get("ab") or {}
    views, leads = m["steps"][0]["n"], m["steps"][1]["n"]
    created = datetime.fromtimestamp(landing["created"]).strftime("%d/%m/%Y")
    score = rep["score"]
    tone = "good" if score >= 85 else "mid" if score >= 60 else "bad"

    cluster = assets.get("cluster", {})
    pilar = cluster.get("pilar", {})
    articles = "".join(
        f"<tr><td><span class='tag {e(x.get('etapa', '').lower())}'>{e(x.get('etapa', ''))}</span></td><td><b>{e(x.get('titulo', ''))}</b>"
        f"<small>{e(x.get('angulo', ''))}</small></td><td>{e(x.get('keyword', ''))}</td><td>{e(x.get('intencion', ''))}</td></tr>"
        for x in cluster.get("articulos", []))
    magnets = "".join(f"<div class='card'><span class='tag'>{e(x.get('formato', ''))}</span><b>{e(x.get('titulo', ''))}</b>"
                      f"<p>{e(x.get('por_que', ''))}</p></div>" for x in assets.get("lead_magnets", []))
    ads = "".join(f"<div class='card'><span class='tag'>{e(x.get('formato', ''))}</span><b>“{e(x.get('hook', ''))}”</b>"
                  f"<p>{e(x.get('angulo', ''))}</p></div>" for x in assets.get("anuncios", []))
    emails = "".join(f"<div class='card'><span class='tag'>Dia {e(str(x.get('dia', '')))} · {e(x.get('objetivo', ''))}</span>"
                     f"<b>{e(x.get('asunto', ''))}</b><p>{e(x.get('cuerpo', ''))}</p></div>" for x in assets.get("emails", []))
    faq = "".join(f"<details><summary>{e(q.get('pregunta', ''))}</summary><p>{e(q.get('respuesta', ''))}</p></details>"
                  for q in s.get("faq", []))
    channels = "".join(f"<span>{e(k)}: <b>{n}</b></span>" for k, n in (landing.get("channels") or {}).items()) or "<span>Sin leads aun</span>"

    def rate(x):
        return f"{100 * x['leads'] / x['views']:.1f}%" if x["views"] else "—"

    ab_html = ""
    if s.get("titular_b"):
        va, vb = v.get("A", {"views": 0, "leads": 0}), v.get("B", {"views": 0, "leads": 0})
        ab_html = f"""<section><h2>Test A/B del titular</h2>
<p class="muted">{'Activo: 50/50 por visitante (cookie de 30 dias), misma URL canonica.' if landing.get('ab_active') else 'Inactivo: se activa desde el panel.'}</p>
<div class="grid2">
 <div class="card"><span class="tag">A · {e(s.get('angulo_a', ''))}</span><b>{e(s.get('titular_a', ''))}</b><p>{va['views']} visitas · {va['leads']} leads · <b>{rate(va)}</b></p></div>
 <div class="card"><span class="tag">B · {e(s.get('angulo_b', ''))}</span><b>{e(s.get('titular_b', ''))}</b><p>{vb['views']} visitas · {vb['leads']} leads · <b>{rate(vb)}</b></p></div>
</div><p class="muted">{e(ab.get('message', ''))}</p></section>"""

    if not s:
        body = "<section><p>Esta landing es anterior al agente. Usa <b>Optimizar con el agente</b> en el panel para generar estrategia, embudo y SEO.</p></section>"
    else:
        body = f"""
<section><h2>Resumen estrategico</h2>
<p class="big">{e(s.get('big_idea', ''))}</p>
<div class="grid3">
 <div class="card"><span class="tag">Framework</span><b>{e(s.get('framework', ''))}</b><p>{e(s.get('por_que_framework', ''))}</p></div>
 <div class="card"><span class="tag">Nivel de consciencia</span><b>{e(a.get('nivel_consciencia', ''))}</b><p>Escala de Eugene Schwartz: define cuanto educar antes de vender.</p></div>
 <div class="card"><span class="tag">Oferta</span><b>{e(p.get('nombre', ''))}</b><p>{e(p.get('tipo', ''))} · {_money(p.get('precio'))}</p></div>
</div></section>

<section><h2>Avatar: {e(a.get('nombre', ''))}</h2><p class="muted">{e(a.get('perfil', ''))}</p>
<div class="grid3">
 <div class="card"><span class="tag">Dolores</span><ul>{_li(a.get('dolores'))}</ul></div>
 <div class="card"><span class="tag">Deseos</span><ul>{_li(a.get('deseos'))}</ul></div>
 <div class="card"><span class="tag">Objeciones</span><ul>{_li(a.get('objeciones'))}</ul></div>
</div></section>

{_critic_section(landing.get('critic'))}

{_funnel_section(landing)}

<section><h2>Origen de los leads</h2><div class="pills">{channels}</div>
<p class="muted">Por utm_source. Desde ManyChat el boton debe apuntar a <code>{e(url)}?utm_source=manychat&amp;mc_id={{{{user_id}}}}</code> para etiquetar al suscriptor en cada paso.</p></section>

<section><h2>Estrategia SEO</h2>
<div class="grid2">
 <div>
  <p><span class="tag">Keyword principal</span><br><b class="kw">{e(seo.get('keyword_principal', ''))}</b> <small>intencion {e(seo.get('intencion', ''))}</small></p>
  <p><span class="tag">Secundarias</span></p><div class="pills">{''.join(f'<span>{e(k)}</span>' for k in seo.get('keywords_secundarias', []))}</div>
  <p><span class="tag">Long-tail</span></p><div class="pills">{''.join(f'<span>{e(k)}</span>' for k in seo.get('long_tail', []))}</div>
 </div>
 <div><p><span class="tag">Asi se ve en Google</span></p>
  <div class="serp"><cite>{e(url)}</cite><h3>{e(seo.get('meta_title', ''))}</h3><p>{e(seo.get('meta_description', ''))}</p></div></div>
</div>
<div class="grid2">
 <div><h3>Auditoria SEO: <span class="score {tone}">{score}/100</span></h3><ul class="checks">{''.join(_check(c) for c in rep['checks'])}</ul></div>
 <div><h3>Auditoria de conversion: {conv['passed']}/{conv['total']}</h3><ul class="checks">{''.join(_check(c) for c in conv['checks'])}</ul></div>
</div>
<p class="muted">Inyectado automaticamente: title, meta description, canonical, Open Graph, Twitter Card y JSON-LD
({'Course' if p.get('tipo') in ('curso', 'mentoria', 'masterclass') else 'Product'} con oferta + FAQPage). La landing figura en <code>/sitemap.xml</code>.</p>
</section>

{ab_html}

<section><h2>Cluster de contenido</h2>
<p class="muted">Una landing de venta rara vez posiciona sola: el trafico organico llega por contenido que la enlaza.</p>
<div class="card pilar"><span class="tag">Articulo pilar</span><b>{e(pilar.get('titulo', ''))}</b><p>Keyword: {e(pilar.get('keyword', ''))}</p></div>
<div class="tablewrap"><table><thead><tr><th>Etapa</th><th>Articulo</th><th>Keyword</th><th>Intencion</th></tr></thead><tbody>{articles}</tbody></table></div>
</section>

{_video_section(landing.get('funnel') or {})}

<section><h2>Lead magnets</h2><div class="grid3">{magnets}</div></section>
<section><h2>Hooks para anuncios</h2><div class="grid3">{ads}</div></section>
<section><h2>Secuencia de emails (dia 0-7)</h2><p class="muted">Viaja con cada lead al webhook de n8n, que la programa con esperas por lead.</p><div class="grid2">{emails}</div></section>
<section><h2>Preguntas frecuentes (landing y schema)</h2>{faq}</section>

{_trace_section(landing.get('trace'))}

<section class="note"><h2>Notas metodologicas</h2><ul>
<li>Las keywords son una propuesta de la IA basada en el lenguaje del avatar. Validar volumen y dificultad con Google Keyword Planner o Search Console antes de producir contenido.</li>
<li>Desde 2023 Google muestra resultados enriquecidos de FAQ casi solo en sitios de gobierno y salud. El schema FAQPage se mantiene porque ayuda a buscadores y motores de respuesta con IA a entender la oferta.</li>
<li>Con links de pago reales, una compra se registra cuando el checkout redirige a su URL de exito. Para conciliar ingresos exactos, conectar el webhook de Stripe o Hotmart a n8n.</li>
<li>Los testimonios de la landing son de ejemplo y deben sustituirse por reales antes de invertir en trafico.</li>
</ul></section>"""

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Estrategia · {e(landing['title'])}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.55 system-ui,sans-serif;background:#f4f6f8;color:#17202b}}
.wrap{{max-width:1040px;margin:0 auto;padding:28px 20px 60px}}a{{color:#0b5cad}}
header.top{{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-end;margin-bottom:20px}}
h1{{margin:0;font-size:26px;line-height:1.2}}h2{{font-size:19px;margin:0 0 12px}}h3{{font-size:16px;margin:18px 0 10px}}
.muted,small{{color:#566372}}small{{display:block;font-size:12.5px}}
section{{background:#fff;border:1px solid #dde3ea;border-radius:12px;padding:20px;margin-bottom:16px}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}}
.kpi{{background:#fff;border:1px solid #dde3ea;border-radius:12px;padding:14px}}.kpi b{{display:block;font-size:22px}}.kpi span{{color:#566372;font-size:13px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
@media(max-width:820px){{.grid2,.grid3{{grid-template-columns:1fr 1fr}}.kpis{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:520px){{.grid2,.grid3,.kpis{{grid-template-columns:1fr 1fr}}.grid2,.grid3{{grid-template-columns:1fr}}}}
.card{{border:1px solid #e4e9ef;border-radius:10px;padding:14px;background:#fbfcfd}}.card b{{display:block;margin:6px 0 4px}}.card p{{margin:0;color:#3d4a59}}
.card ul,.card ol{{margin:6px 0 0;padding-left:18px}}.pilar{{margin-bottom:12px;border-color:#0b6bcb}}.pr{{font-size:20px;font-weight:700;color:#17202b!important}}
.tag{{display:inline-block;font-size:11.5px;font-weight:600;color:#0b4f96;background:#e6f0fb;border-radius:999px;padding:2px 9px}}
.tag.tofu{{background:#e7f6ec;color:#17663a}}.tag.mofu{{background:#fff3dc;color:#8a5a00}}.tag.bofu{{background:#fde8e8;color:#9b1c1c}}
.big{{font-size:18px;font-weight:600;margin:0 0 14px}}.kw{{font-size:18px}}
.pills{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}}.pills span{{background:#eef1f5;border-radius:6px;padding:3px 8px;font-size:13px}}
.serp{{border:1px solid #e4e9ef;border-radius:10px;padding:14px;background:#fff;font-family:arial,sans-serif}}
.serp cite{{color:#202124;font-style:normal;font-size:13px;word-break:break-all}}.serp h3{{color:#1a0dab;font-size:19px;font-weight:400;margin:4px 0}}.serp p{{color:#4d5156;margin:0;font-size:14px}}
.checks{{list-style:none;padding:0;margin:0}}.checks li{{padding:3px 0}}
.ok{{color:#17803d;font-weight:700}}.ko{{color:#c02626;font-weight:700}}
.score{{padding:2px 10px;border-radius:999px}}.score.good{{background:#e7f6ec;color:#17663a}}.score.mid{{background:#fff3dc;color:#8a5a00}}.score.bad{{background:#fde8e8;color:#9b1c1c}}
.tablewrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:9px 8px;border-top:1px solid #e9edf2;vertical-align:top}}th{{color:#566372;font-weight:600}}
details{{border-top:1px solid #e9edf2;padding:8px 0}}summary{{cursor:pointer;font-weight:600}}details p{{margin:6px 0 0;color:#3d4a59}}
.flow{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px;font-size:14px}}.flow a{{background:#17202b;color:#fff;padding:6px 12px;border-radius:8px;text-decoration:none}}.flow span{{color:#566372}}
.fstep{{margin:8px 0}}.flabel{{font-size:14px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}}.flabel small{{display:inline}}
.fbar{{height:10px;background:#eef1f5;border-radius:9px;overflow:hidden;margin-top:3px}}.fbar i{{display:block;height:100%;background:#0b6bcb;border-radius:9px}}
.trace{{list-style:none;padding:0;margin:0;font-size:13.5px}}.trace li{{padding:5px 0;border-top:1px solid #eef1f5}}.trace code{{color:#566372}}
.k-fix .tag{{background:#fff3dc;color:#8a5a00}}.k-check .tag{{background:#e7f6ec;color:#17663a}}.k-warn .tag,.k-error .tag{{background:#fde8e8;color:#9b1c1c}}.k-done .tag,.k-handoff .tag{{background:#17202b;color:#fff}}
.note{{background:#fafbfc}}.btns a,.btns button{{font:inherit;font-size:14px;border:1px solid #c9d2dc;background:#fff;border-radius:8px;padding:7px 12px;color:#17202b;text-decoration:none;cursor:pointer}}
@media print{{.btns{{display:none}}body{{background:#fff}}section,.kpi{{break-inside:avoid}}}}
</style></head><body><div class="wrap">
<header class="top"><div><small>Informe de embudo · {created}</small><h1>{e(landing['title'])}</h1><small><a href="{e(url)}" target="_blank" rel="noopener">{e(url)}</a></small></div>
<div class="btns"><button onclick="print()">Imprimir / PDF</button> <a href="/api/landings/{e(slug)}/strategy">JSON</a> <a href="/">Panel</a></div></header>
<div class="kpis"><div class="kpi"><span>SEO on-page</span><b>{score}/100</b></div><div class="kpi"><span>Conversion (checks)</span><b>{conv['passed']}/{conv['total']}</b></div>
<div class="kpi"><span>Leads</span><b>{leads}</b> <small>{views} visitas</small></div><div class="kpi"><span>Ingresos</span><b>{_money(m['revenue'])}</b></div>
<div class="kpi"><span>Ticket medio</span><b>{_money(m['aov'])}</b></div></div>
{body}
</div></body></html>"""

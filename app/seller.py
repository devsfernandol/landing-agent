"""Agente vendedor: responde dudas y objeciones de los visitantes por chat y los lleva a dejar su email.

Un solo cerebro, dos canales:
  - chat flotante en la landing (POST /api/seller/{slug})
  - ManyChat / FunnelChat por "External Request" (POST /api/manychat/{slug}/reply), para Instagram, WhatsApp o Messenger
Solo usa lo que el embudo ya sabe (ficha del producto, avatar, objeciones, FAQ y el texto publicado): si algo no esta, no lo inventa.
"""
import json
from html import escape as e

from .agent import visible_copy
from .llm import call_llm_json
from .prompts import SELLER_SYSTEM

MAX_REPLY = 700
FALLBACK = {"respuesta": "Ahora mismo no puedo responderte. Deja tu email en el formulario y el equipo te escribe con la respuesta.",
            "mostrar_boton": True}


def context(landing: dict, html: str) -> str:
    s = landing.get("strategy") or {}
    p, av = s.get("producto") or {}, s.get("avatar") or {}
    ficha = {"producto": {k: p.get(k) for k in ("nombre", "tipo", "precio", "moneda", "descripcion")},
             "para_quien": av.get("perfil", ""), "dolores": av.get("dolores", []), "deseos": av.get("deseos", []),
             "objeciones_tipicas": av.get("objeciones", []), "faq": s.get("faq", []), "brief_del_creador": landing.get("brief", "")}
    return f"FICHA:\n{json.dumps(ficha, ensure_ascii=False)}\n\nLANDING (texto publicado):\n{visible_copy(html, 6000)}"


async def reply(landing: dict, html: str, messages: list[dict]) -> dict:
    convo = "\n".join(f"{'VISITANTE' if m['role'] == 'user' else 'TU'}: {m['content']}" for m in messages[-10:])
    try:
        out = await call_llm_json(f"{context(landing, html)}\n\nCONVERSACION:\n{convo}\n\nResponde al ultimo mensaje del VISITANTE.",
                                  SELLER_SYSTEM, max_tokens=400)
    except Exception as ex:  # el visitante nunca ve un error tecnico
        print(f"[vendedor] {landing['slug']}: {type(ex).__name__}: {ex}", flush=True)
        return dict(FALLBACK)
    text = str(out.get("respuesta") or "").strip()
    if not text:
        return dict(FALLBACK)
    return {"respuesta": text[:MAX_REPLY], "mostrar_boton": bool(out.get("mostrar_boton"))}


def manychat(text: str, url: str = "", show_button: bool = False) -> dict:
    """Respuesta dinamica de ManyChat (formato v2): el bot de Instagram/WhatsApp contesta con el mismo agente."""
    msg = {"type": "text", "text": text}
    if url and show_button:
        msg["buttons"] = [{"type": "url", "caption": "Ver el programa", "url": url}]
    return {"version": "v2", "content": {"messages": [msg]}}


def widget(slug: str, product: str, t: dict, questions: list[str], preview: bool) -> str:
    """Chat flotante autocontenido (sin dependencias) con los colores de la landing."""
    cfg = json.dumps({"slug": slug, "product": product, "questions": questions[:3], "preview": preview}, ensure_ascii=False)
    cfg = cfg.replace("</", "<\\/")
    ac, on = e(t.get("accent", "#111111")), e(t.get("on_accent", "#ffffff"))
    return f"""<style>
#la-chat{{position:fixed;right:18px;bottom:18px;z-index:2147483000;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1b2430}}
#la-chat *{{box-sizing:border-box}}
#la-chat [hidden]{{display:none!important}}
#la-chat .lc-fab{{display:flex;align-items:center;gap:8px;border:0;border-radius:999px;padding:13px 18px;background:{ac};color:{on};font:inherit;font-weight:700;cursor:pointer;box-shadow:0 8px 24px #0003}}
#la-chat .lc-tip{{position:absolute;right:0;bottom:62px;background:#fff;border-radius:12px;padding:10px 14px;box-shadow:0 8px 24px #0002;white-space:nowrap;font-size:14px}}
#la-chat .lc-box{{position:absolute;right:0;bottom:64px;width:min(370px,calc(100vw - 24px));height:min(540px,calc(100vh - 110px));background:#fff;border-radius:16px;box-shadow:0 18px 50px #0004;display:flex;flex-direction:column;overflow:hidden}}
#la-chat .lc-hd{{background:{ac};color:{on};padding:14px 16px;display:flex;justify-content:space-between;align-items:center;gap:8px}}
#la-chat .lc-hd small{{display:block;opacity:.85;font-weight:400}}
#la-chat .lc-x{{background:none;border:0;color:inherit;font-size:22px;cursor:pointer;line-height:1}}
#la-chat .lc-log{{flex:1;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:8px;background:#f6f7f9}}
#la-chat .lc-m{{max-width:85%;padding:9px 12px;border-radius:14px;white-space:pre-wrap;overflow-wrap:anywhere}}
#la-chat .lc-bot{{background:#fff;border:1px solid #e3e6ea;align-self:flex-start;border-bottom-left-radius:4px}}
#la-chat .lc-me{{background:{ac};color:{on};align-self:flex-end;border-bottom-right-radius:4px}}
#la-chat .lc-chips{{display:flex;flex-wrap:wrap;gap:6px}}
#la-chat .lc-chip{{border:1px solid {ac};color:#1b2430;background:#fff;border-radius:999px;padding:6px 10px;font:inherit;font-size:13px;cursor:pointer;text-align:left}}
#la-chat .lc-cta{{align-self:flex-start;border:0;border-radius:10px;padding:10px 14px;background:{ac};color:{on};font:inherit;font-weight:700;cursor:pointer}}
#la-chat form{{display:flex;gap:6px;padding:10px;border-top:1px solid #e3e6ea;background:#fff}}
#la-chat input{{flex:1;min-width:0;border:1px solid #cfd5dc;border-radius:10px;padding:10px;font:inherit;font-size:16px}}
#la-chat .lc-send{{border:0;border-radius:10px;padding:0 14px;background:{ac};color:{on};font:inherit;font-weight:700;cursor:pointer}}
#la-chat button:focus-visible,#la-chat input:focus-visible{{outline:3px solid {ac};outline-offset:2px}}
#la-chat .lc-dots{{opacity:.6;font-style:italic}}
@media (max-width:480px){{#la-chat{{right:12px;bottom:12px}}#la-chat .lc-box{{bottom:60px}}}}
</style>
<div id="la-chat"></div>
<script>(function(){{
const C={cfg},K='la_chat_'+C.slug,root=document.getElementById('la-chat');
let msgs=[];try{{msgs=JSON.parse(sessionStorage.getItem(K)||'[]')}}catch(_){{}}
const save=()=>{{try{{sessionStorage.setItem(K,JSON.stringify(msgs.slice(-12)))}}catch(_){{}}}};
const el=(t,c,x)=>{{const n=document.createElement(t);if(c)n.className=c;if(x!=null)n.textContent=x;return n}};
const fab=el('button','lc-fab','💬 ¿Dudas? Pregúntame');fab.setAttribute('aria-expanded','false');
const box=el('div','lc-box');box.hidden=true;box.setAttribute('role','dialog');box.setAttribute('aria-label','Chat con el asistente');
const hd=el('div','lc-hd');const ttl=el('div');ttl.append(el('b',null,'Asistente de '+C.product),el('small',null,'Responde al instante'));
const x=el('button','lc-x','×');x.setAttribute('aria-label','Cerrar chat');hd.append(ttl,x);
const log=el('div','lc-log');log.setAttribute('aria-live','polite');
const f=el('form');const inp=el('input');inp.placeholder='Escribe tu pregunta…';inp.maxLength=500;inp.setAttribute('aria-label','Tu pregunta');
const send=el('button','lc-send','Enviar');send.type='submit';f.append(inp,send);
box.append(hd,log,f);root.append(box,fab);
function goForm(){{const fm=document.querySelector('#leadForm')||document.querySelector('form:not(#la-chat form)');toggle(false);
  if(fm){{fm.scrollIntoView({{behavior:'smooth',block:'center'}});const em=fm.querySelector('input[type=email]');if(em)setTimeout(()=>em.focus(),500)}}}}
function bubble(m){{log.append(el('div','lc-m '+(m.role==='user'?'lc-me':'lc-bot'),m.content));
  if(m.cta){{const b=el('button','lc-cta','Quiero apuntarme →');b.type='button';b.onclick=goForm;log.append(b)}}log.scrollTop=log.scrollHeight}}
function draw(){{log.innerHTML='';bubble({{role:'assistant',content:'Hola 👋 Soy el asistente de '+C.product+'. ¿Qué te gustaría saber?'}});
  if(!msgs.length&&C.questions.length){{const ch=el('div','lc-chips');C.questions.forEach(q=>{{const b=el('button','lc-chip',q);b.type='button';b.onclick=()=>ask(q);ch.append(b)}});log.append(ch)}}
  msgs.forEach(bubble)}}
function toggle(open){{box.hidden=!open;fab.setAttribute('aria-expanded',open);fab.textContent=open?'✕ Cerrar':'💬 ¿Dudas? Pregúntame';
  const tip=root.querySelector('.lc-tip');if(tip)tip.remove();if(open){{draw();inp.focus()}}}}
async function ask(q){{q=q.trim();if(!q||send.disabled)return;inp.value='';msgs.push({{role:'user',content:q}});save();draw();
  const dots=el('div','lc-m lc-bot lc-dots','Escribiendo…');log.append(dots);log.scrollTop=log.scrollHeight;send.disabled=true;
  try{{const r=await fetch('/api/seller/'+C.slug,{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{messages:msgs.map(m=>({{role:m.role,content:m.content}})),preview:C.preview}})}});
    const d=await r.json();msgs.push({{role:'assistant',content:r.ok?d.respuesta:(d.detail||'Ahora mismo no puedo responder.'),cta:!!(r.ok&&d.mostrar_boton)}})}}
  catch(_){{msgs.push({{role:'assistant',content:'Sin conexión. Inténtalo de nuevo en un momento.'}})}}
  finally{{send.disabled=false;save();draw();inp.focus()}}}}
fab.onclick=()=>toggle(box.hidden);x.onclick=()=>toggle(false);f.onsubmit=ev=>{{ev.preventDefault();ask(inp.value)}};
document.addEventListener('keydown',ev=>{{if(ev.key==='Escape'&&!box.hidden)toggle(false)}});
setTimeout(()=>{{if(box.hidden&&!msgs.length){{const t=el('div','lc-tip','¿Tienes alguna duda? Te respondo ahora 👋');t.onclick=()=>toggle(true);root.append(t)}}}},7000);
}})();</script>"""

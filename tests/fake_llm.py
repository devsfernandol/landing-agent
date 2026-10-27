"""LLM simulado: respuestas fijas por prompt de sistema. La landing sale con fallos a proposito
(sin keyword en el H1, pocos H2, sin precio) para que el agente tenga que autocorregirse."""
from app import prompts

STRATEGY = {
    "producto": {"nombre": "Excel Pro Contable", "tipo": "curso", "precio": 147, "moneda": "USD", "marca": "Excel Pro",
                 "descripcion": "Curso online de Excel para contadores: tablas dinamicas, Power Query y dashboards."},
    "avatar": {"nombre": "Marta, 34", "perfil": "Contadora en una pyme.", "dolores": ["cierres eternos", "errores manuales", "reportes tardios"],
               "deseos": ["salir a tiempo", "reportes automaticos", "ascender"], "objeciones": ["no tengo tiempo", "no se programar", "es caro"],
               "nivel_consciencia": "consciente de la solucion"},
    "framework": "PAS", "por_que_framework": "Conoce el problema y busca solucion.", "big_idea": "Tu cierre mensual en una tarde.",
    "titular_a": "Termina el cierre mensual en una tarde", "titular_b": "Curso de Excel para contadores que automatiza tus reportes",
    "angulo_a": "resultado", "angulo_b": "keyword directa",
    "seo": {"keyword_principal": "curso de excel para contadores", "intencion": "transaccional",
            "keywords_secundarias": ["tablas dinamicas", "power query", "dashboards en excel", "excel contable", "automatizar reportes"],
            "long_tail": ["curso de excel para contadores online"], "meta_title": "Excel",
            "meta_description": "Aprende Excel.", "h2_sugeridos": ["Tablas dinamicas", "Power Query", "Dashboards"]},
    "faq": [{"pregunta": "Necesito saber programar?", "respuesta": "No."},
            {"pregunta": "Cuanto tiempo necesito por semana?", "respuesta": "Tres horas."},
            {"pregunta": "Sirve con Excel 2016?", "respuesta": "Si."}],
}
ASSETS = {"cluster": {"pilar": {"titulo": "Guia de Excel contable", "keyword": "excel contable"},
                      "articulos": [{"titulo": "Que es Power Query", "keyword": "power query", "etapa": "TOFU", "intencion": "informacional", "angulo": "x"}]},
          "lead_magnets": [{"titulo": "Plantilla de cierre", "formato": "plantilla", "por_que": "x"}],
          "anuncios": [{"hook": "Tu cierre te roba 3 dias", "angulo": "dolor", "formato": "reel"}],
          "emails": [{"dia": 0, "asunto": "Bienvenida", "objetivo": "bienvenida", "cuerpo": "Hola"}]}
FUNNEL = {"upsell": {"nombre": "Pack de plantillas contables", "precio": 297, "precio_ancla": 450, "titular": "Agrega las plantillas",
                     "subtitular": "Listas para usar", "bullets": ["a", "b", "c", "d"], "por_que_ahora": "Solo aqui",
                     "cta_si": "Si, agregar", "cta_no": "No, prefiero armarlas yo"},
          "downsell": {"nombre": "Plantillas basicas", "precio": 97, "titular": "Version basica", "subtitular": "x",
                       "bullets": ["a", "b", "c"], "cta_si": "Si", "cta_no": "No"},
          "gracias": {"titular": "Gracias", "mensaje": "Revisa tu email", "pasos": ["1", "2", "3"]},
          "vsl": {"duracion": "4 min", "escenas": [{"tiempo": "0:00-0:20", "bloque": "gancho", "visual": "x", "voz": "y"}]},
          "reels": [{"hook": "h", "escenas": ["a"], "cta": "c", "duracion": "30 s"}]}
META = {"meta_title": "Curso de excel para contadores: reportes en minutos",
        "meta_description": "Curso de excel para contadores con tablas dinamicas, Power Query y dashboards. Cierra el mes en una tarde. Inscribete hoy con garantia de 14 dias."}

FORM = """<form id="f"><label>Nombre<input name="name"></label><label>Email<input type="email" name="email"></label>
<button type="submit">Quiero inscribirme</button></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();await fetch("/api/lead",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:"x",email:"y",slug:"__SLUG__"})})}</script>"""
WORDS = " ".join(["Automatiza conciliaciones, reportes y cierres con metodos probados."] * 60)
FAQ = "".join(f"<h3>{q['pregunta']}</h3><p>{q['respuesta']}</p>" for q in STRATEGY["faq"])
STYLE = "<style>:root{--bg:#fdfaf3;--ink:#1d1d1f;--accent:#0f766e}body{font:16px/1.5 'Inter',sans-serif}h1{font-family:'Fraunces',serif}</style>"

# primera version con fallos; la reescritura del agente los corrige
LANDING_V1 = f"""<!DOCTYPE html><html><head><meta charset="utf-8">{STYLE}</head><body><header><a href="#f">Inscribirme</a></header>
<main><h1>Termina el cierre mensual en una tarde</h1><section><h2>Tu problema</h2><p>{WORDS}</p></section>
<section>{FAQ}</section><section><p>Garantia de 14 dias.</p><a href="#f">Quiero entrar</a>{FORM}</section></main>
<footer>Testimonios de ejemplo</footer></body></html>"""
LANDING_V2 = f"""<!DOCTYPE html><html><head><meta charset="utf-8">{STYLE}</head><body><header><a href="#f">Inscribirme</a></header>
<main><h1>Curso de Excel para contadores: termina el cierre en una tarde</h1><p>El curso de excel para contadores que automatiza tu mes.</p>
<section><h2>Tablas dinamicas</h2><p>{WORDS}</p></section><section><h2>Power Query</h2></section><section><h2>Dashboards en Excel</h2></section>
<section>{FAQ}</section><section><p>Precio: 147 USD. Garantia de 14 dias.</p><a href="#f">Quiero entrar</a>{FORM}</section></main>
<footer>Testimonios de ejemplo</footer></body></html>"""

CRITIC_LOW = {"secciones": [{"seccion": "Titular y promesa", "nota": 8, "motivo": "claro", "mejora": ""},
                           {"seccion": "Manejo de objeciones", "nota": 5, "motivo": "no responde 'no tengo tiempo'",
                            "mejora": "Agrega un bloque que muestre que bastan 3 horas por semana"}],
              "objecion_sin_responder": "no tengo tiempo", "veredicto": "rehacer", "resumen": "Falta tratar el tiempo."}
CRITIC_OK = {"secciones": [{"seccion": "Titular y promesa", "nota": 8, "motivo": "claro", "mejora": ""},
                          {"seccion": "Manejo de objeciones", "nota": 8, "motivo": "responde el tiempo", "mejora": ""}],
             "objecion_sin_responder": "", "veredicto": "aprobar", "resumen": "Lista para trafico."}

calls: list[str] = []


async def call_llm_json(prompt, system, max_tokens=4000):
    name = {prompts.STRATEGY_SYSTEM: "strategy", prompts.ASSETS_SYSTEM: "assets", prompts.FUNNEL_SYSTEM: "funnel",
            prompts.META_FIX_SYSTEM: "meta", prompts.CRITIC_SYSTEM: "critic"}[system]
    calls.append(name)
    import copy
    if name == "critic":  # rechaza la v1 (sin precio visible) y aprueba la v2
        return copy.deepcopy(CRITIC_OK if "147 USD" in prompt else CRITIC_LOW)
    return copy.deepcopy({"strategy": STRATEGY, "assets": ASSETS, "funnel": FUNNEL, "meta": META}[name])


async def call_llm(prompt, system, json_mode=False, max_tokens=8000):
    if system == prompts.LANDING_SYSTEM:
        calls.append("landing")
        return LANDING_V1
    calls.append("refine")
    return LANDING_V2

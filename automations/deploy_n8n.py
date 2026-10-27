"""Despliega en n8n (por su API publica) el workflow que programa y envia los emails del embudo.

    ./venv/bin/python automations/deploy_n8n.py

Requiere en .env: N8N_API_URL, N8N_API_KEY. Opcional: N8N_GMAIL_CREDENTIAL_ID (si no, usa la primera credencial
de Gmail que encuentre en tus workflows). Crea o actualiza el workflow por nombre, lo activa y escribe en .env
N8N_WEBHOOK_URL y AUTOMATION_KEY (la clave con la que n8n consulta a la app). Es idempotente.
"""
import re
import secrets
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
NAME = "Landing Agent · Emails del embudo (recuperacion + entrega)"
WEBHOOK_PATH = "landing-agent-embudo"
HEADER_CRED = "Landing Agent · clave de automatizacion"

# Solo nodos nativos (sin Code): no dependen del task runner de JavaScript de la instancia
EVENT = "$('Evento del embudo').first().json.body"
QUERY = (("lead", f"{EVENT}.lead_id"), ("kind", "$json.kind"), ("n", "$json.n ?? 0"), ("item", "$json.item || 'main'"))

NOTE = """## Emails del embudo
**La app decide, n8n programa y envia.**
1. Cada lead o compra llega con su `schedule`.
2. Por cada email: espera hasta `send_at` y, justo antes de enviar, pregunta a la app `/api/automation/email`.
3. La app responde `send=false` si ya compro (corta la recuperacion) o se dio de baja; si no, devuelve el HTML con la marca del embudo.
4. Gmail lo envia.

Secuencias: **recuperacion** (5 emails dia 0-7 si no compra) · **acceso** inmediato por producto comprado · **seguimiento** dia 3."""


def env_set(key: str, value: str) -> None:
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    line = f"{key}={value}"
    text = re.sub(rf"^{key}=.*$", line, text, flags=re.M) if re.search(rf"^{key}=", text, re.M) else text.rstrip("\n") + f"\n{line}\n"
    ENV.write_text(text, encoding="utf-8")


def node(name, type_, version, pos, params, **extra):
    return {"id": str(uuid.uuid4()), "name": name, "type": type_, "typeVersion": version, "position": pos,
            "parameters": params, **extra}


def build(gmail_cred: dict, header_cred: dict) -> dict:
    nodes = [
        node("Nota", "n8n-nodes-base.stickyNote", 1, [-60, -260], {"content": NOTE, "width": 560, "height": 300}),
        node("Evento del embudo", "n8n-nodes-base.webhook", 2, [0, 120],
             {"httpMethod": "POST", "path": WEBHOOK_PATH, "responseMode": "onReceived", "options": {}}, webhookId=str(uuid.uuid4())),
        node("Programar emails", "n8n-nodes-base.splitOut", 1, [220, 120],
             {"fieldToSplitOut": "body.schedule", "include": "noOtherFields", "options": {}}),
        node("Un email a la vez", "n8n-nodes-base.splitInBatches", 3, [440, 120], {"options": {}}),
        node("Esperar al momento de envio", "n8n-nodes-base.wait", 1.1, [660, 220],
             {"resume": "timeInterval", "amount": "={{ Math.max(1, Math.round($json.send_at - Date.now() / 1000)) }}",
              "unit": "seconds"}, webhookId=str(uuid.uuid4())),
        node("Pedir email a la app", "n8n-nodes-base.httpRequest", 4.2, [880, 220],
             {"url": f"={{{{ {EVENT}.api_base }}}}/api/automation/email", "authentication": "genericCredentialType",
              "genericAuthType": "httpHeaderAuth", "sendQuery": True,
              "queryParameters": {"parameters": [{"name": q, "value": f"={{{{ {v} }}}}"} for q, v in QUERY]},
              "options": {}},
             credentials={"httpHeaderAuth": header_cred}, onError="continueRegularOutput"),
        node("Corresponde enviar?", "n8n-nodes-base.if", 2, [1100, 220],
             {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                             "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.send }}", "rightValue": "",
                                             "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                             "combinator": "and"}, "options": {}}),
        node("Enviar con Gmail", "n8n-nodes-base.gmail", 2.1, [1320, 140],
             {"sendTo": "={{ $json.to }}", "subject": "={{ $json.subject }}", "emailType": "html",
              "message": "={{ $json.html }}", "options": {"appendAttribution": False}},
             credentials={"gmailOAuth2": gmail_cred}),
    ]
    to = lambda n: [{"node": n, "type": "main", "index": 0}]
    connections = {
        "Evento del embudo": {"main": [to("Programar emails")]},
        "Programar emails": {"main": [to("Un email a la vez")]},
        "Un email a la vez": {"main": [[], to("Esperar al momento de envio")]},
        "Esperar al momento de envio": {"main": [to("Pedir email a la app")]},
        "Pedir email a la app": {"main": [to("Corresponde enviar?")]},
        "Corresponde enviar?": {"main": [to("Enviar con Gmail"), to("Un email a la vez")]},
        "Enviar con Gmail": {"main": [to("Un email a la vez")]},
    }
    return {"name": NAME, "nodes": nodes, "connections": connections, "settings": {"executionOrder": "v1"}}


def find_gmail(api: httpx.Client, workflows: list[dict]) -> dict | None:
    """Reutiliza la credencial de Gmail que ya usa algun workflow (la API no expone su contenido, solo el id)."""
    for w in workflows:
        for n in api.get(f"/workflows/{w['id']}").json().get("nodes", []):
            if (n.get("credentials") or {}).get("gmailOAuth2"):
                return n["credentials"]["gmailOAuth2"]
    return None


def main() -> None:
    cfg = dotenv_values(ENV)
    base, key = (cfg.get("N8N_API_URL") or "").rstrip("/"), cfg.get("N8N_API_KEY") or ""
    if not base or not key:
        sys.exit("Faltan N8N_API_URL y N8N_API_KEY en .env")
    api = httpx.Client(base_url=f"{base}/api/v1", headers={"X-N8N-API-KEY": key}, timeout=30)

    workflows = api.get("/workflows", params={"limit": 250}).json()["data"]
    gmail = {"id": cfg["N8N_GMAIL_CREDENTIAL_ID"], "name": "Gmail"} if cfg.get("N8N_GMAIL_CREDENTIAL_ID") else find_gmail(api, workflows)
    if not gmail:
        sys.exit("No encontre una credencial de Gmail: crea una en n8n o define N8N_GMAIL_CREDENTIAL_ID")

    automation_key = cfg.get("AUTOMATION_KEY") or secrets.token_urlsafe(32)
    # credencial de cabecera: se recrea en cada despliegue para que siempre coincida con .env
    listed = api.get("/credentials")
    for c in listed.json().get("data", []) if listed.status_code == 200 else []:
        if c.get("name") == HEADER_CRED:
            api.delete(f"/credentials/{c['id']}")
    r = api.post("/credentials", json={"name": HEADER_CRED, "type": "httpHeaderAuth",
                                       "data": {"name": "X-Automation-Key", "value": automation_key,
                                                "allowedHttpRequestDomains": "all"}})
    r.raise_for_status()
    header = {"id": r.json()["id"], "name": HEADER_CRED}

    body = build(gmail, header)
    existing = next((w for w in workflows if w["name"] == NAME), None)
    if existing:
        api.post(f"/workflows/{existing['id']}/deactivate")
        r = api.put(f"/workflows/{existing['id']}", json=body)
    else:
        r = api.post("/workflows", json=body)
    if r.status_code >= 400:
        sys.exit(f"n8n rechazo el workflow ({r.status_code}): {r.text[:500]}")
    wf_id = r.json()["id"]
    r = api.post(f"/workflows/{wf_id}/activate")
    if r.status_code >= 400:
        sys.exit(f"Workflow creado ({wf_id}) pero no se pudo activar ({r.status_code}): {r.text[:500]}")

    env_set("AUTOMATION_KEY", automation_key)
    env_set("N8N_WEBHOOK_URL", f"{base}/webhook/{WEBHOOK_PATH}")
    print(f"Workflow activo: {base}/workflow/{wf_id}")
    print(f"Webhook: {base}/webhook/{WEBHOOK_PATH}  (guardado en .env junto con AUTOMATION_KEY)")
    print("Reinicia el servidor de la app para que tome los cambios de .env")


if __name__ == "__main__":
    main()

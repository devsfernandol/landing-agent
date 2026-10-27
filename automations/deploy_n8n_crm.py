"""Despliega en n8n el workflow que recibe los contactos del embudo (leads, compras, reembolsos, carritos) para el CRM.

    ./venv/bin/python automations/deploy_n8n_crm.py

Requiere en .env: N8N_API_URL, N8N_API_KEY y AUTOMATION_KEY (y haber corrido antes deploy_n8n.py, que crea la credencial
de cabecera). El webhook queda protegido con esa clave: en el panel (Integraciones) el webhook debe enviar la cabecera
X-Automation-Key. Cada rama termina en un nodo "Conecta aqui tu CRM": se reemplaza por HubSpot, Pipedrive, Brevo,
GoHighLevel o una peticion HTTP cuando se elija el CRM. Es idempotente (crea o actualiza por nombre).
"""
import sys
import uuid

import httpx
from dotenv import dotenv_values

from deploy_n8n import ENV, HEADER_CRED, node

NAME = "Landing Agent · CRM (leads, compras, reembolsos y carritos)"
WEBHOOK_PATH = "landing-agent-crm"
B = "$json.body"
STAGES = (("lead.created", "Lead"), ("purchase.completed", "Cliente"), ("purchase.refunded", "Reembolsado"),
          ("cart.abandoned", "Carrito abandonado"))

NOTE = """## Contactos del embudo → CRM
La app envia un contacto por cada **lead, compra, reembolso o carrito abandonado** (firmado y con reintentos).
1. **Contacto para el CRM** lo deja en campos planos: email, nombre, etapa, producto, importe, UTM.
2. **Segun la etapa** lo separa en 4 ramas.
3. Reemplaza cada **Conecta aqui tu CRM** por el nodo de tu CRM (HubSpot, Pipedrive, Brevo, GoHighLevel...)
   o por una peticion HTTP: *crear o actualizar contacto por email* y *mover a la etapa*.

El campo `id` del evento es unico: usalo para no duplicar si el CRM lo permite."""


def field(name: str, value: str, type_: str = "string") -> dict:
    return {"id": str(uuid.uuid4()), "name": name, "value": value, "type": type_}


def build(header_cred: dict) -> dict:
    fields = [field("evento", f"={{{{ {B}.event }}}}"), field("etapa", f"={{{{ {B}.stage }}}}"),
              field("email", f"={{{{ {B}.contact.email }}}}"), field("nombre", f"={{{{ {B}.contact.first_name }}}}"),
              field("apellido", f"={{{{ {B}.contact.last_name }}}}"),
              field("producto", f"={{{{ {B}.product ? {B}.product.name : '' }}}}"),
              field("importe", f"={{{{ {B}.product ? {B}.product.price : null }}}}", "number"),
              field("moneda", f"={{{{ {B}.product ? {B}.product.currency : '' }}}}"),
              field("landing", f"={{{{ {B}.landing.title }}}}"), field("landing_url", f"={{{{ {B}.landing.url }}}}"),
              field("utm_source", f"={{{{ {B}.utm.utm_source }}}}"), field("utm_campaign", f"={{{{ {B}.utm.utm_campaign }}}}"),
              field("fecha", f"={{{{ {B}.occurred_at }}}}"), field("id_evento", f"={{{{ {B}.id }}}}"),
              field("es_prueba", f"={{{{ {B}.test === true }}}}", "boolean")]
    rules = [{"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                             "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.evento }}", "rightValue": ev,
                                             "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"},
              "renameOutput": True, "outputKey": stage} for ev, stage in STAGES]
    nodes = [
        node("Nota", "n8n-nodes-base.stickyNote", 1, [-80, -300], {"content": NOTE, "width": 600, "height": 300}),
        node("Contacto del embudo", "n8n-nodes-base.webhook", 2, [0, 120],
             {"httpMethod": "POST", "path": WEBHOOK_PATH, "authentication": "headerAuth", "responseMode": "onReceived",
              "options": {}}, webhookId=str(uuid.uuid4()), credentials={"httpHeaderAuth": header_cred}),
        node("Contacto para el CRM", "n8n-nodes-base.set", 3.4, [240, 120],
             {"mode": "manual", "assignments": {"assignments": fields}, "options": {}}),
        node("Segun la etapa", "n8n-nodes-base.switch", 3, [480, 120], {"rules": {"values": rules}, "options": {}}),
    ]
    targets = []
    for i, (_, stage) in enumerate(STAGES):
        name = f"Conecta aqui tu CRM · {stage}"
        nodes.append(node(name, "n8n-nodes-base.noOp", 1, [760, -60 + i * 120], {}))
        targets.append([{"node": name, "type": "main", "index": 0}])
    connections = {
        "Contacto del embudo": {"main": [[{"node": "Contacto para el CRM", "type": "main", "index": 0}]]},
        "Contacto para el CRM": {"main": [[{"node": "Segun la etapa", "type": "main", "index": 0}]]},
        "Segun la etapa": {"main": targets},
    }
    return {"name": NAME, "nodes": nodes, "connections": connections, "settings": {"executionOrder": "v1"}}


def main() -> None:
    cfg = dotenv_values(ENV)
    base, key = (cfg.get("N8N_API_URL") or "").rstrip("/"), cfg.get("N8N_API_KEY") or ""
    if not base or not key:
        sys.exit("Faltan N8N_API_URL y N8N_API_KEY en .env")
    api = httpx.Client(base_url=f"{base}/api/v1", headers={"X-N8N-API-KEY": key}, timeout=30)
    listed = api.get("/credentials")
    cred = next((c for c in (listed.json().get("data", []) if listed.status_code == 200 else []) if c.get("name") == HEADER_CRED), None)
    if not cred:
        sys.exit(f"No encontre la credencial '{HEADER_CRED}': corre antes automations/deploy_n8n.py")

    body = build({"id": cred["id"], "name": HEADER_CRED})
    workflows = api.get("/workflows", params={"limit": 250}).json()["data"]
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
    print(f"Workflow activo: {base}/workflow/{wf_id}")
    print(f"Webhook para el panel (Integraciones): {base}/webhook/{WEBHOOK_PATH}  · cabecera X-Automation-Key = AUTOMATION_KEY")


if __name__ == "__main__":
    main()

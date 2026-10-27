"""Meta Conversions API: el servidor envia Lead y Purchase directamente a Meta (no los bloquean adblockers ni iOS).

El pixel del navegador y el servidor envian el mismo evento con el mismo event_id (lead.<id>, purchase.<id>.<producto>):
Meta se queda con uno solo. Los datos personales viajan hasheados (SHA-256), como exige Meta. Se configura en el panel
(Integraciones) o con META_PIXEL_ID y META_CAPI_TOKEN; con un "codigo de prueba" los eventos aparecen en vivo en
Administrador de eventos > Probar eventos. Cada envio queda en el registro de Integraciones.
"""
import hashlib
import os
import time

import httpx

from . import db
from .config import META_PIXEL_ID

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v23.0")
LOG_ID, LOG_NAME = "meta-capi", "Meta Conversions API"


def config() -> dict:
    s = db.get_setting("meta_capi", {}) or {}
    return {"pixel_id": s.get("pixel_id") or META_PIXEL_ID, "token": s.get("token") or os.getenv("META_CAPI_TOKEN", "").strip(),
            "test_code": s.get("test_code", ""), "active": s.get("active", True)}


def pixel_id() -> str:
    return config()["pixel_id"]


def enabled() -> bool:
    c = config()
    return bool(c["active"] and c["pixel_id"] and c["token"])


def _h(v) -> str:
    return hashlib.sha256(str(v).strip().lower().encode()).hexdigest()


def user_data(lead: dict) -> dict:
    """Coincidencia con la cuenta de Meta: email, nombre e id hasheados; IP, navegador y cookies _fbp/_fbc tal cual."""
    first = (lead.get("name") or "").strip().split(" ")[0]
    ud = {"em": [_h(lead["email"])] if lead.get("email") else None, "fn": [_h(first)] if first else None,
          "external_id": [_h(lead["id"])] if lead.get("id") else None, "client_ip_address": lead.get("ip") or None,
          "client_user_agent": lead.get("ua") or None, "fbp": lead.get("fbp") or None, "fbc": lead.get("fbc") or None}
    return {k: v for k, v in ud.items() if v}


def event(name: str, event_id: str, lead: dict, url: str, custom: dict | None = None) -> dict:
    ev = {"event_name": name, "event_time": int(time.time()), "event_id": event_id, "action_source": "website",
          "event_source_url": url, "user_data": user_data(lead)}
    if custom:
        ev["custom_data"] = {k: v for k, v in custom.items() if v is not None}
    return ev


async def send(ev: dict, test_code: str | None = None) -> dict:
    c = config()
    body = {"data": [ev]}
    code = c["test_code"] if test_code is None else test_code
    if code:
        body["test_event_code"] = code
    status, error = 0, ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{c['pixel_id']}/events",
                                  params={"access_token": c["token"]}, json=body)
        status = r.status_code
        if status >= 400:
            error = str(((r.json() or {}).get("error") or {}).get("message") or r.text)[:200]
    except (httpx.HTTPError, ValueError) as ex:
        error = f"{type(ex).__name__}: {ex}"[:200]
    ok = 200 <= status < 300
    db.add_delivery(hook_id=LOG_ID, hook_name=LOG_NAME + (" (prueba)" if code else ""), url=f"pixel {c['pixel_id']}",
                    event=ev["event_name"], email=(ev.get("user_data") or {}).get("em") and "(hasheado)" or "",
                    status=status, ok=int(ok), attempts=1, error=error)
    return {"ok": ok, "status": status, "error": error}


async def track(name: str, event_id: str, lead: dict | None, url: str, custom: dict | None = None) -> None:
    """Se llama en segundo plano tras un lead o una compra; sin configuracion no hace nada."""
    if lead and enabled():
        await send(event(name, event_id, lead, url, custom))

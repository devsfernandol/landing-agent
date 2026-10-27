"""Webhooks salientes listos para un CRM: cada lead, compra, reembolso o carrito abandonado se envia como un contacto.

Se configuran en el panel (Integraciones): URL, eventos y cabeceras opcionales (p. ej. la clave del webhook de n8n).
Cada envio lleva la firma X-Landing-Signature: t=<timestamp>,v1=<hmac-sha256 de "t.cuerpo"> con el secreto del webhook
(el mismo esquema que Stripe), se reintenta si falla y queda registrado para verlo en el panel.
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timezone

import httpx

from . import db
from .config import PUBLIC_URL

EVENTS = {"lead.created": "Nuevo lead", "purchase.completed": "Compra confirmada",
          "purchase.refunded": "Reembolso o contracargo", "cart.abandoned": "Carrito abandonado (Hotmart)"}
STAGE = {"lead.created": "Lead", "purchase.completed": "Cliente", "purchase.refunded": "Reembolsado",
         "cart.abandoned": "Carrito abandonado"}
RETRIES = (0, 3, 15)  # segundos antes de cada intento


def hooks() -> list[dict]:
    return db.get_setting("webhooks", [])


def save(items: list[dict]) -> list[dict]:
    """Conserva el secreto de los existentes y crea uno para los nuevos."""
    old = {h["id"]: h for h in hooks()}
    out = []
    for h in items:
        prev = old.get(h.get("id") or "")
        out.append({"id": prev["id"] if prev else uuid.uuid4().hex[:12], "name": h.get("name") or "Webhook",
                    "url": h["url"], "events": [e for e in h.get("events", []) if e in EVENTS], "active": bool(h.get("active", True)),
                    "headers": {k: v for k, v in (h.get("headers") or {}).items() if k and v},
                    "secret": prev["secret"] if prev else secrets.token_hex(24)})
    db.set_setting("webhooks", out)
    return out


def _price(landing: dict, item: str) -> tuple:
    s, f = landing.get("strategy") or {}, landing.get("funnel") or {}
    p = s.get("producto") or {}
    src = p if item == "main" else (f.get(item) or {})
    return src.get("precio"), p.get("moneda") or "USD"


def contact(event: str, landing: dict, lead: dict | None, item: str = "", extra: dict | None = None) -> dict:
    """Formato estable y plano, pensado para mapearlo a cualquier CRM (HubSpot, Pipedrive, Brevo, GoHighLevel...)."""
    from .emails import product  # evita import circular
    lead = lead or {}
    name = (lead.get("name") or "").strip()
    first, _, last = name.partition(" ")
    body = {"id": uuid.uuid4().hex, "event": event, "stage": STAGE.get(event, ""),
            "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "landing-agent",
            "contact": {"email": lead.get("email", ""), "name": name, "first_name": first, "last_name": last},
            "landing": {"slug": landing["slug"], "title": landing["title"], "url": f"{PUBLIC_URL}/p/{landing['slug']}"},
            "utm": {k: lead.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign")},
            "lead_id": lead.get("id"), "variant": lead.get("variant", "")}
    if item:
        price, currency = _price(landing, item)
        body["product"] = {"item": item, "name": product(landing, item)["nombre"], "price": price, "currency": currency}
    return {**body, **(extra or {})}


def signature(body: bytes, secret: str, t: int | None = None) -> str:
    t = t or int(time.time())
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


async def deliver(hook: dict, payload: dict, retries: tuple | None = None) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode()
    retries = RETRIES if retries is None else retries
    status, error, n = 0, "", 0
    for n, wait in enumerate(retries, 1):
        if wait:
            await asyncio.sleep(wait)
        headers = {"Content-Type": "application/json", "User-Agent": "landing-agent-webhooks/1",
                   "X-Landing-Event": payload["event"], "X-Landing-Signature": signature(body, hook["secret"]), **hook.get("headers", {})}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(hook["url"], content=body, headers=headers)
            status, error = r.status_code, "" if r.status_code < 400 else r.text[:200]
            if r.status_code < 400 or 400 <= r.status_code < 500 and r.status_code != 429:
                break  # exito, o un error del receptor que no se arregla reintentando
        except httpx.HTTPError as ex:
            status, error = 0, f"{type(ex).__name__}: {ex}"[:200]
    ok = 200 <= status < 300
    db.add_delivery(hook_id=hook["id"], hook_name=hook["name"], url=hook["url"], event=payload["event"],
                    email=(payload.get("contact") or {}).get("email", ""), status=status, ok=int(ok), attempts=n, error=error)
    return ok


async def emit(event: str, payload: dict) -> None:
    """Envia a todos los webhooks activos suscritos al evento (en paralelo; un fallo no afecta a los demas)."""
    targets = [h for h in hooks() if h.get("active") and event in h.get("events", [])]
    if targets:
        await asyncio.gather(*(deliver(h, payload) for h in targets))

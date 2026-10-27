"""Integraciones salientes: webhook de n8n (o Make) y API de ManyChat.

ManyChat: el boton del flujo apunta a la landing con ?utm_source=manychat&mc_id={{user_id}}.
Con ese id, cada hito del embudo etiqueta al suscriptor en ManyChat para que el bot siga la conversacion
(recordatorio si no compra, bienvenida si compra). Ningun fallo externo interrumpe al visitante.
"""
import hashlib
from urllib.parse import urlsplit

import httpx

from . import db
from .config import MANYCHAT_API_KEY, N8N_WEBHOOK_URL, PUBLIC_URL

MANYCHAT_API = "https://api.manychat.com/fb/subscriber"
# evento del embudo -> etiqueta en ManyChat
MANYCHAT_TAGS = {"lead": "landing_lead", "purchase_main": "comprador", "purchase_upsell": "comprador_upsell",
                 "purchase_downsell": "comprador_downsell"}


async def send_n8n(payload: dict) -> bool:
    if not N8N_WEBHOOK_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(N8N_WEBHOOK_URL, json=payload)
            return r.status_code < 400
    except httpx.HTTPError:
        return False


async def manychat_tag(mc_id: str, event: str, fields: dict | None = None) -> bool:
    tag = MANYCHAT_TAGS.get(event)
    if not (MANYCHAT_API_KEY and mc_id.isdigit() and tag):
        return False
    headers = {"Authorization": f"Bearer {MANYCHAT_API_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            r = await client.post(f"{MANYCHAT_API}/addTagByName", json={"subscriber_id": int(mc_id), "tag_name": tag})
            for name, value in (fields or {}).items():
                await client.post(f"{MANYCHAT_API}/setCustomFieldByName",
                                  json={"subscriber_id": int(mc_id), "field_name": name, "field_value": value})
            return r.status_code < 400
    except httpx.HTTPError:
        return False


# ---------- IndexNow: avisa a Bing, Yandex, Seznam y Naver en cuanto se publica una landing ----------
def indexnow_key() -> str:
    """Clave estable derivada del secreto de la app; se publica en /indexnow.txt para demostrar que el dominio es nuestro."""
    return hashlib.sha256(f"indexnow:{db.secret()}".encode()).hexdigest()[:32]


async def indexnow(urls: list[str]) -> bool:
    if not PUBLIC_URL.startswith("https://") or not urls:
        return False  # en local o en pruebas no se avisa a nadie
    body = {"host": urlsplit(PUBLIC_URL).hostname, "key": indexnow_key(),
            "keyLocation": f"{PUBLIC_URL}/indexnow.txt", "urlList": urls}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post("https://api.indexnow.org/indexnow", json=body)
            return r.status_code in (200, 202)
    except httpx.HTTPError:
        return False

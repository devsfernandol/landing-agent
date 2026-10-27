"""Pagos verificados: la compra la confirma la pasarela (Stripe o Hotmart), no la redireccion del navegador.

El embudo agrega al link de pago una referencia firmada (lead + producto) y el email del lead:
  Stripe   ?client_reference_id=<ref>&prefilled_email=<email>   -> vuelve en checkout.session.completed
  Hotmart  ?sck=<ref>&email=<email>&name=<nombre>               -> vuelve en el postback (data.purchase.origin.sck)
Asi se sabe que lead compro que producto sin confiar en la URL de exito, que es publica y se puede escribir a mano.
"""
import hashlib
import json
import hmac
import time
from urllib.parse import urlencode

from . import emails

TOLERANCE = 300  # segundos: Stripe recomienda rechazar firmas antiguas para evitar reenvios


def is_stripe(url: str) -> bool:
    return url.startswith(("https://buy.stripe.com/", "https://checkout.stripe.com/"))


def is_hotmart(url: str) -> bool:
    return url.startswith(("https://pay.hotmart.com/", "https://go.hotmart.com/"))


def provider(url: str) -> str:
    return "stripe" if is_stripe(url) else "hotmart" if is_hotmart(url) else ""


def client_ref(lead_id: int, slug: str, item: str, prov: str = "stripe") -> str:
    """Solo letras, numeros, - y _ (lo que admiten Stripe y el sck de Hotmart): lead_slug_item_firma."""
    sig = emails.sign(lead_id, slug, item, prov).rsplit(".", 1)[1]
    return f"{lead_id}_{slug}_{item}_{sig}"


def parse_ref(ref: str, prov: str = "stripe") -> tuple[int, str, str] | None:
    parts = ref.split("_")
    if len(parts) != 4 or not parts[0].isdigit():
        return None
    lead_id, slug, item, sig = parts
    ok = emails.verify(f"{lead_id}.{slug}.{item}.{prov}.{sig}")
    return (int(lead_id), slug, item) if ok else None


def link_for(url: str, lead: dict | None, slug: str, item: str) -> str:
    prov = provider(url)
    if not (lead and prov):
        return url
    ref = client_ref(lead["id"], slug, item, prov)
    params = ({"client_reference_id": ref, "prefilled_email": lead["email"]} if prov == "stripe"
              else {"sck": ref, "email": lead["email"], **({"name": lead["name"]} if lead.get("name") else {})})
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


# ---------- Hotmart (postback 2.0) ----------
HOTMART_PAID = {"PURCHASE_APPROVED", "PURCHASE_COMPLETE"}
HOTMART_REVOKED = {"PURCHASE_REFUNDED", "PURCHASE_CHARGEBACK", "PURCHASE_CANCELED", "PURCHASE_PROTEST"}


HOTMART_LABELS = {
    "PURCHASE_APPROVED": "Compra aprobada", "PURCHASE_COMPLETE": "Compra completada", "PURCHASE_REFUNDED": "Reembolso",
    "PURCHASE_CHARGEBACK": "Contracargo", "PURCHASE_CANCELED": "Compra cancelada", "PURCHASE_PROTEST": "Pedido de reembolso",
    "PURCHASE_BILLET_PRINTED": "Pago pendiente (boleto/transferencia)", "PURCHASE_DELAYED": "Compra atrasada",
    "PURCHASE_EXPIRED": "Compra expirada", "PURCHASE_OUT_OF_SHOPPING_CART": "Abandono de carrito",
    "SUBSCRIPTION_CANCELLATION": "Cancelacion de suscripcion", "SWITCH_PLAN": "Cambio de plan",
    "UPDATE_SUBSCRIPTION_CHARGE_DATE": "Cambio de fecha de cobro", "CLUB_FIRST_ACCESS": "Primer acceso al curso",
    "CLUB_MODULE_COMPLETED": "Modulo completado", "ORDER_FULFILLMENT": "Datos logisticos (producto fisico)",
}
HOTMART_CATEGORY = {"PURCHASE_APPROVED": "compra", "PURCHASE_COMPLETE": "compra", "PURCHASE_REFUNDED": "reverso",
                    "PURCHASE_CHARGEBACK": "reverso", "PURCHASE_CANCELED": "reverso", "PURCHASE_PROTEST": "reverso",
                    "PURCHASE_OUT_OF_SHOPPING_CART": "carrito", "PURCHASE_BILLET_PRINTED": "pendiente",
                    "PURCHASE_DELAYED": "pendiente", "PURCHASE_EXPIRED": "pendiente"}
# Los eventos de "Prueba tu configuracion" usan productos y compradores ficticios
TEST_MARKERS = ("test postback", "product name com", "@example.com", "teste@hotmart.com")


def hotmart_info(payload: dict) -> dict:
    """Lo relevante del postback 2.0, tolerante a campos que falten segun el tipo de evento."""
    data = payload.get("data") or {}
    product, buyer, purchase = data.get("product") or {}, data.get("buyer") or {}, data.get("purchase") or {}
    price = purchase.get("price") or {}
    amount = f"{price.get('value')} {price.get('currency_value') or ''}".strip() if price.get("value") is not None else ""
    name = str(product.get("name") or "")
    return {"event": str(payload.get("event") or ""), "product": name, "product_id": str(product.get("id") or ""),
            "ucode": str(product.get("ucode") or ""), "offer": str((purchase.get("offer") or {}).get("code") or ""),
            "transaction": str(purchase.get("transaction") or ""), "email": str(buyer.get("email") or "").strip().lower(),
            "name": str(buyer.get("name") or ""), "amount": amount,
            "test": is_test(payload)}


def is_test(payload: dict) -> bool:
    """Pruebas de Hotmart: producto o comprador ficticio en cualquier parte del aviso (algunos eventos no traen producto)."""
    blob = json.dumps(payload.get("data") or {}, ensure_ascii=False).lower()
    return any(m in blob for m in TEST_MARKERS)


def hotmart_item(landing: dict, info: dict) -> str:
    """Sin sck: que producto del embudo es, comparando el link de pago guardado con el codigo de la oferta o del producto."""
    for item, link in (landing.get("checkout") or {}).items():
        if link and any(code and code in link for code in (info["offer"], info["ucode"], info["product_id"])):
            return item
    return "main"


def hotmart_sck(payload: dict) -> str:
    """El sck viaja en data.purchase.origin.sck; se aceptan tambien variantes vistas en integraciones antiguas."""
    data = payload.get("data") or {}
    purchase = data.get("purchase") or {}
    for v in ((purchase.get("origin") or {}).get("sck"), purchase.get("sck"), data.get("sck"), payload.get("sck")):
        if isinstance(v, str) and v:
            return v
    return ""


def signature_ok(body: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Verificacion del header Stripe-Signature (t=...,v1=...) segun la documentacion de Stripe, sin depender del SDK."""
    items = [kv.split("=", 1) for kv in header.split(",") if "=" in kv]
    t = next((v for k, v in items if k == "t"), "")
    sigs = [v for k, v in items if k == "v1"]
    if not t.isdigit() or not sigs or abs((now or time.time()) - int(t)) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)

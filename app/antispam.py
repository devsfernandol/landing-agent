"""Proteccion del formulario publico: cada lead dispara emails desde la cuenta del negocio,
asi que un bot no debe poder usarlo para enviar spam a terceros.

Capas: token firmado por visita (sin pagina no hay lead), tiempo minimo de llenado,
limite por IP y global, email valido y sin duplicados.
"""
import re
import time
from collections import defaultdict, deque

from fastapi import Request

from .config import LEAD_MIN_SECONDS, LEAD_RATE_GLOBAL, LEAD_RATE_PER_IP
from .emails import sign, verify

EMAIL_RE = re.compile(r"^[^@\s<>\"'(),;:]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}$")
TOKEN_MAX_AGE = 86400
WINDOW = 600  # 10 minutos


def form_token(slug: str) -> str:
    return sign(int(time.time()), slug)


def token_problem(token: str, slug: str) -> str | None:
    parts = verify(token or "")
    if not parts or parts[1:] != [slug] or not parts[0].isdigit():
        return "token"
    age = time.time() - int(parts[0])
    if age < LEAD_MIN_SECONDS:
        return "demasiado rapido"
    if age > TOKEN_MAX_AGE:
        return "token vencido"
    return None


def client_ip(request: Request) -> str:
    """Detras de Traefik la IP real llega en X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: int = WINDOW) -> bool:
        now, q = time.time(), self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter()


def rate_ok(ip: str) -> bool:
    return limiter.allow(f"ip:{ip}", LEAD_RATE_PER_IP) and limiter.allow("global", LEAD_RATE_GLOBAL)

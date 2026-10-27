"""Configuracion leida del entorno (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE = Path(os.getenv("DATA_DIR", ROOT))
PAGES = BASE / "pages"
ASSETS = PAGES / "assets"
DB_PATH = BASE / "landing_agent.db"
# Archivos de la version anterior: se importan una sola vez a SQLite
LEGACY_STATS = BASE / "stats.json"
LEGACY_LEADS = BASE / "leads.jsonl"

PAGES.mkdir(parents=True, exist_ok=True)
ASSETS.mkdir(exist_ok=True)

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").lower()
# USD por millon de tokens, para mostrar el costo de cada tarea (ajustables si cambia la tarifa del proveedor)
LLM_PRICE_IN = float(os.getenv("LLM_PRICE_IN", "3" if PROVIDER == "claude" else "0.28"))
LLM_PRICE_OUT = float(os.getenv("LLM_PRICE_OUT", "15" if PROVIDER == "claude" else "0.42"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "").strip()
MANYCHAT_API_KEY = os.getenv("MANYCHAT_API_KEY", "").strip()
# n8n llama a /api/automation/* con esta clave (cabecera X-Automation-Key)
AUTOMATION_KEY = os.getenv("AUTOMATION_KEY", "").strip()
# Webhook de Stripe (whsec_...): con el, las compras por Payment Link las confirma Stripe y no la URL de exito
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
# Hotmart (hottok del webhook 2.0): con el, las compras por link de Hotmart las confirma Hotmart
HOTMART_HOTTOK = os.getenv("HOTMART_HOTTOK", "").strip()
# Duracion de un "dia" en las secuencias de email. 86400 en produccion; 60 para una demo en vivo (un dia = un minuto)
EMAIL_DAY_SECONDS = int(os.getenv("EMAIL_DAY_SECONDS", "86400"))

# Anti-spam del formulario publico (cada lead dispara emails desde tu Gmail)
LEAD_MIN_SECONDS = float(os.getenv("LEAD_MIN_SECONDS", "3"))      # un humano tarda mas en llenar el formulario
LEAD_RATE_PER_IP = int(os.getenv("LEAD_RATE_PER_IP", "5"))        # leads por IP cada 10 minutos
LEAD_RATE_GLOBAL = int(os.getenv("LEAD_RATE_GLOBAL", "60"))       # tope total cada 10 minutos (Gmail ~500/dia)

# Medicion (opcionales): se inyectan en todas las landings al servirlas
META_PIXEL_ID = os.getenv("META_PIXEL_ID", "").strip()
GA4_ID = os.getenv("GA4_ID", "").strip()

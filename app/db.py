"""Persistencia en SQLite: landings, variantes A/B y leads."""
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_PATH, LEGACY_LEADS, LEGACY_STATS, PAGES

SCHEMA = """
CREATE TABLE IF NOT EXISTS landings (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    brief TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL,
    logo INTEGER NOT NULL DEFAULT 0,
    imgs INTEGER NOT NULL DEFAULT 0,
    colors TEXT NOT NULL DEFAULT '[]',
    framework TEXT NOT NULL DEFAULT '',
    strategy TEXT,
    seo_score INTEGER,
    seo_report TEXT,
    ab_active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS variants (
    slug TEXT NOT NULL,
    variant TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slug, variant)
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL DEFAULT '',
    variant TEXT NOT NULL DEFAULT 'A',
    name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS leads_slug ON leads (slug, variant);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    event TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS events_slug ON events (slug, event);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    item TEXT NOT NULL,
    ts INTEGER NOT NULL,
    UNIQUE (lead_id, slug, item)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    provider TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    event TEXT NOT NULL,
    item TEXT NOT NULL DEFAULT '',
    product TEXT NOT NULL DEFAULT '',
    transaction_id TEXT NOT NULL DEFAULT '',
    buyer TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    test INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS payment_events_slug ON payment_events (slug, ts);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    hook_id TEXT NOT NULL,
    hook_name TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    event TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 1,
    error TEXT NOT NULL DEFAULT ''
);
"""

# Columnas agregadas despues de la primera version: se crean al arrancar si faltan
MIGRATIONS = {
    "landings": {"funnel": "TEXT", "checkout": "TEXT NOT NULL DEFAULT '{}'", "trace": "TEXT", "conv_report": "TEXT",
                 "delivery": "TEXT NOT NULL DEFAULT '{}'", "critic": "TEXT", "usage": "TEXT",
                 "seller_chat": "INTEGER NOT NULL DEFAULT 1"},
    "leads": {"utm_source": "TEXT NOT NULL DEFAULT ''", "utm_medium": "TEXT NOT NULL DEFAULT ''",
              "utm_campaign": "TEXT NOT NULL DEFAULT ''", "mc_id": "TEXT NOT NULL DEFAULT ''",
              "unsubscribed": "INTEGER NOT NULL DEFAULT 0",
              # Meta Conversions API: mejoran la coincidencia de las compras que llegan despues por webhook
              "fbp": "TEXT NOT NULL DEFAULT ''", "fbc": "TEXT NOT NULL DEFAULT ''", "ip": "TEXT NOT NULL DEFAULT ''",
              "ua": "TEXT NOT NULL DEFAULT ''"},
}
JSON_COLS = ("colors", "strategy", "seo_report", "funnel", "checkout", "trace", "conv_report", "delivery", "critic", "usage")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        c.execute("PRAGMA journal_mode=WAL")
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        if not c.execute("SELECT 1 FROM meta WHERE key='legacy_imported'").fetchone():
            _import_legacy(c)
            c.execute("INSERT INTO meta VALUES ('legacy_imported', ?)", (str(int(time.time())),))


def _import_legacy(c: sqlite3.Connection) -> None:
    """Importa stats.json, leads.jsonl y cualquier landing en pages/ que no estuviera registrada."""
    try:
        stats = json.loads(LEGACY_STATS.read_text(encoding="utf-8"))
    except Exception:
        stats = {}
    for f in PAGES.glob("*.html"):
        if f.name.endswith((".prev.html", ".b.html")):
            continue
        slug = f.stem
        s = stats.get(slug, {})
        title = s.get("title")
        if not title:
            m = re.search(r"<title>(.*?)</title>", f.read_text(encoding="utf-8", errors="ignore"), re.S | re.I)
            title = (m.group(1).strip() if m else slug)[:70]
        c.execute(
            "INSERT OR IGNORE INTO landings (slug, title, brief, created, logo, imgs, colors) VALUES (?,?,?,?,?,?,?)",
            (slug, title, s.get("title", title), s.get("created", int(f.stat().st_mtime)),
             int(s.get("logo", False)), s.get("imgs", 0), json.dumps(s.get("colors", []))),
        )
        c.execute("INSERT OR IGNORE INTO variants VALUES (?, 'A', ?)", (slug, s.get("views", 0)))
    if LEGACY_LEADS.exists():
        for line in LEGACY_LEADS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            c.execute("INSERT INTO leads (slug, name, email, ts) VALUES (?,?,?,?)",
                      (r.get("slug", ""), r.get("name", ""), r["email"], r.get("ts", 0)))


# ---------- landings ----------
def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_COLS:
        d[k] = json.loads(d[k]) if d.get(k) else None
    d["colors"] = d["colors"] or []
    d["checkout"] = d["checkout"] or {}
    d["delivery"] = d["delivery"] or {}
    d["ab_active"] = bool(d["ab_active"])
    d["seller_chat"] = bool(d.get("seller_chat", 1))
    return d


def create_landing(slug: str, **fields) -> None:
    fields = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    cols = ["slug", *fields]
    with conn() as c:
        c.execute(f"INSERT INTO landings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                  (slug, *fields.values()))
        c.execute("INSERT OR IGNORE INTO variants VALUES (?, 'A', 0)", (slug,))


def update_landing(slug: str, **fields) -> None:
    fields = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    with conn() as c:
        c.execute(f"UPDATE landings SET {', '.join(f'{k}=?' for k in fields)} WHERE slug=?",
                  (*fields.values(), slug))


def get_landing(slug: str):
    with conn() as c:
        r = c.execute("SELECT * FROM landings WHERE slug=?", (slug,)).fetchone()
    return _row(r) if r else None


def list_landings() -> list[dict]:
    with conn() as c:
        rows = [_row(r) for r in c.execute("SELECT * FROM landings ORDER BY created DESC")]
    for r in rows:
        r["variants"] = variant_stats(r["slug"])
    return rows


def delete_landing(slug: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM landings WHERE slug=?", (slug,))
        c.execute("DELETE FROM variants WHERE slug=?", (slug,))
        c.execute("DELETE FROM events WHERE slug=?", (slug,))
        # los leads se conservan: son del negocio, no de la pagina


# ---------- visitas, leads y A/B ----------
def add_view(slug: str, variant: str) -> None:
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO variants VALUES (?, ?, 0)", (slug, variant))
        c.execute("UPDATE variants SET views = views + 1 WHERE slug=? AND variant=?", (slug, variant))


def add_lead(slug: str, variant: str, name: str, email: str, utm: dict, mc_id: str = "", meta: dict | None = None) -> dict:
    rec = {"slug": slug, "variant": variant, "name": name, "email": email, "ts": int(time.time()),
           "utm_source": utm.get("utm_source", ""), "utm_medium": utm.get("utm_medium", ""),
           "utm_campaign": utm.get("utm_campaign", ""), "mc_id": mc_id,
           **{k: str(v)[:300] for k, v in (meta or {}).items() if k in ("fbp", "fbc", "ip", "ua") and v}}
    with conn() as c:
        rec["id"] = c.execute(f"INSERT INTO leads ({','.join(rec)}) VALUES ({','.join(':' + k for k in rec)})", rec).lastrowid
    return rec


def recent_lead(email: str, slug: str, within: int):
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE email=? AND slug=? AND ts>? ORDER BY id DESC LIMIT 1",
                      (email, slug, int(time.time()) - within)).fetchone()
    return dict(r) if r else None


def get_lead(lead_id: int):
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    return dict(r) if r else None


def unsubscribe(lead_id: int) -> None:
    """La baja aplica al email en todos los embudos, no solo al registro."""
    with conn() as c:
        c.execute("UPDATE leads SET unsubscribed=1 WHERE email=(SELECT email FROM leads WHERE id=?)", (lead_id,))


def add_purchase(lead_id: int, slug: str, item: str) -> bool:
    """Devuelve False si esa compra ya estaba registrada (recarga de la pagina de exito)."""
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO purchases (lead_id, slug, item, ts) VALUES (?,?,?,?)",
                        (lead_id, slug, item, int(time.time())))
        return cur.rowcount == 1


def purchased_items(lead_id: int, slug: str) -> set[str]:
    with conn() as c:
        return {r["item"] for r in c.execute("SELECT item FROM purchases WHERE lead_id=? AND slug=?", (lead_id, slug))}


def get_setting(key: str, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (f"setting:{key}",)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(key: str, value) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"setting:{key}", json.dumps(value)))


def secret() -> str:
    """Clave para firmar los links de acceso y de baja. Se crea una vez y vive en la base de datos."""
    with conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key='secret'").fetchone()
        if r:
            return r["value"]
        value = secrets.token_hex(32)
        c.execute("INSERT INTO meta VALUES ('secret', ?)", (value,))
        return value


def add_event(slug: str, event: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO events (slug, event, ts) VALUES (?,?,?)", (slug, event, int(time.time())))


def event_counts(slug: str) -> dict:
    with conn() as c:
        return {r["event"]: r["n"] for r in c.execute(
            "SELECT event, COUNT(*) AS n FROM events WHERE slug=? GROUP BY event", (slug,))}


def channel_counts(slug: str) -> dict:
    """Leads por canal de origen (utm_source); sin UTM cuenta como 'directo'."""
    with conn() as c:
        return {r["src"]: r["n"] for r in c.execute(
            "SELECT COALESCE(NULLIF(utm_source,''),'directo') AS src, COUNT(*) AS n FROM leads WHERE slug=? GROUP BY src ORDER BY n DESC",
            (slug,))}


def list_leads() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT l.*, COALESCE(g.title, '') AS landing FROM leads l LEFT JOIN landings g ON g.slug = l.slug ORDER BY l.ts DESC")]


def variant_stats(slug: str) -> dict:
    with conn() as c:
        views = {r["variant"]: r["views"] for r in c.execute("SELECT variant, views FROM variants WHERE slug=?", (slug,))}
        leads = {r["variant"]: r["n"] for r in c.execute(
            "SELECT variant, COUNT(*) AS n FROM leads WHERE slug=? GROUP BY variant", (slug,))}
    return {v: {"views": views.get(v, 0), "leads": leads.get(v, 0)} for v in sorted(set(views) | set(leads) | {"A"})}


def swap_variants(slug: str) -> None:
    """Al promover B a principal, sus datos pasan a llamarse A (y viceversa) para que sigan pegados a su titular."""
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO variants VALUES (?, 'B', 0)", (slug,))
        for table in ("variants", "leads"):  # en tres pasos: (slug, variant) es clave primaria en variants
            for old, new in (("A", "_"), ("B", "A"), ("_", "B")):
                c.execute(f"UPDATE {table} SET variant=? WHERE slug=? AND variant=?", (new, slug, old))


def activity_since(slug: str, since: int) -> dict:
    """Para la demo en vivo: leads y pasos del embudo desde un momento dado."""
    with conn() as c:
        leads = [dict(r) for r in c.execute(
            "SELECT id, name, email, variant, utm_source, ts FROM leads WHERE slug=? AND ts>=? ORDER BY ts", (slug, since))]
        events = [dict(r) for r in c.execute(
            "SELECT id, event, ts FROM events WHERE slug=? AND ts>=? ORDER BY id", (slug, since))]
    return {"leads": leads, "events": events}


def remove_purchase(lead_id: int, slug: str, item: str) -> bool:
    """Reembolso o contracargo: sin compra registrada, el acceso firmado deja de funcionar y no se envian emails de compra."""
    with conn() as c:
        return c.execute("DELETE FROM purchases WHERE lead_id=? AND slug=? AND item=?", (lead_id, slug, item)).rowcount > 0


def add_payment_event(**f) -> None:
    """Registro de todo lo que avisa la pasarela (Hotmart), con lo que hizo el sistema: se muestra en el panel."""
    rec = {"ts": int(time.time()), "slug": "", "item": "", "product": "", "transaction_id": "", "buyer": "",
           "amount": "", "result": "", "test": 0, **f}
    with conn() as c:
        c.execute(f"INSERT INTO payment_events ({','.join(rec)}) VALUES ({','.join(':' + k for k in rec)})", rec)


def transaction_paid(provider: str, transaction_id: str) -> bool:
    """Si la pasarela aprobo antes esta transaccion: solo entonces un reembolso o cancelacion revierte algo."""
    if not transaction_id:
        return False
    with conn() as c:
        return c.execute("SELECT 1 FROM payment_events WHERE provider=? AND transaction_id=? AND test=0 AND "
                         "event IN ('PURCHASE_APPROVED','PURCHASE_COMPLETE') AND result LIKE 'Compra%' LIMIT 1",
                         (provider, transaction_id)).fetchone() is not None


def list_payment_events(slug: str = "", limit: int = 200) -> list[dict]:
    q = "SELECT e.*, COALESCE(l.title, '') AS landing FROM payment_events e LEFT JOIN landings l ON l.slug = e.slug"
    args: tuple = ()
    if slug:
        q, args = q + " WHERE e.slug=?", (slug,)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY e.id DESC LIMIT ?", (*args, limit))]


def add_delivery(**f) -> None:
    rec = {"ts": int(time.time()), **f}
    with conn() as c:
        c.execute(f"INSERT INTO webhook_deliveries ({','.join(rec)}) VALUES ({','.join(':' + k for k in rec)})", rec)


def list_deliveries(limit: int = 100) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM webhook_deliveries ORDER BY id DESC LIMIT ?", (limit,))]

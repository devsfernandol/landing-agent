"""Cliente minimo para DeepSeek o Claude, con salida en texto o JSON."""
import contextvars
import json
import os
import re

import httpx
from fastapi import HTTPException

from .config import LLM_PRICE_IN, LLM_PRICE_OUT, PROVIDER

# Uso de tokens de la tarea en curso: cada trabajo del agente abre su acumulador y las llamadas
# concurrentes (asyncio.gather) lo comparten, porque heredan el contexto.
USAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage", default=None)


def new_usage() -> dict:
    u = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    USAGE.set(u)
    return u


def _track(tokens_in: int, tokens_out: int) -> None:
    u = USAGE.get()
    if u is None:
        return
    u["calls"] += 1
    u["input_tokens"] += tokens_in
    u["output_tokens"] += tokens_out
    u["usd"] = round((u["input_tokens"] * LLM_PRICE_IN + u["output_tokens"] * LLM_PRICE_OUT) / 1e6, 4)


async def call_llm(user_prompt: str, system: str, json_mode: bool = False, max_tokens: int = 8000) -> str:
    timeout = httpx.Timeout(240.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if PROVIDER == "claude":
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise HTTPException(500, "Falta ANTHROPIC_API_KEY en .env")
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            if r.status_code != 200:
                raise HTTPException(502, f"Claude API {r.status_code}: {r.text[:300]}")
            data = r.json()
            _track(data.get("usage", {}).get("input_tokens", 0), data.get("usage", {}).get("output_tokens", 0))
            return "".join(b.get("text", "") for b in data["content"])

        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise HTTPException(500, "Falta DEEPSEEK_API_KEY en .env")
        body = {
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "max_tokens": min(max_tokens, 8192),
            "temperature": 0.7,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = await client.post("https://api.deepseek.com/chat/completions",
                              headers={"Authorization": f"Bearer {key}"}, json=body)
        if r.status_code != 200:
            raise HTTPException(502, f"DeepSeek API {r.status_code}: {r.text[:300]}")
        data = r.json()
        _track(data.get("usage", {}).get("prompt_tokens", 0), data.get("usage", {}).get("completion_tokens", 0))
        return data["choices"][0]["message"]["content"]


async def call_llm_json(user_prompt: str, system: str, max_tokens: int = 4000) -> dict:
    raw = await call_llm(user_prompt, system, json_mode=True, max_tokens=max_tokens)
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        print("[landing-agent] JSON invalido:", raw[:300], flush=True)
        raise HTTPException(502, "El modelo no devolvio una estrategia valida. Intenta de nuevo.")


async def chat_with_tools(system: str, messages: list[dict], tools: list[dict], run_tool, max_steps: int = 6) -> str:
    """Bucle de agente: el modelo decide que herramienta usar, se ejecuta y se le devuelve el resultado,
    hasta que responde sin pedir herramientas. tools: [{name, description, parameters (JSON Schema)}].
    run_tool(name, args) -> str. messages: historial [{role: user|assistant, content: str}]."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=15.0)) as client:
        if PROVIDER == "claude":
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise HTTPException(500, "Falta ANTHROPIC_API_KEY en .env")
            msgs = [dict(m) for m in messages]
            spec = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
            for _ in range(max_steps):
                r = await client.post("https://api.anthropic.com/v1/messages",
                                      headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                      json={"model": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"), "max_tokens": 2000,
                                            "system": system, "messages": msgs, "tools": spec})
                if r.status_code != 200:
                    raise HTTPException(502, f"Claude API {r.status_code}: {r.text[:300]}")
                data = r.json()
                _track(data.get("usage", {}).get("input_tokens", 0), data.get("usage", {}).get("output_tokens", 0))
                content = data["content"]
                uses = [b for b in content if b.get("type") == "tool_use"]
                if not uses:
                    return "".join(b.get("text", "") for b in content)
                msgs.append({"role": "assistant", "content": content})
                results = [{"type": "tool_result", "tool_use_id": u["id"], "content": await run_tool(u["name"], u.get("input") or {})}
                           for u in uses]
                msgs.append({"role": "user", "content": results})
        else:
            key = os.getenv("DEEPSEEK_API_KEY")
            if not key:
                raise HTTPException(500, "Falta DEEPSEEK_API_KEY en .env")
            msgs = [{"role": "system", "content": system}] + [dict(m) for m in messages]
            spec = [{"type": "function", "function": t} for t in tools]
            for _ in range(max_steps):
                r = await client.post("https://api.deepseek.com/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                      json={"model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), "messages": msgs,
                                            "tools": spec, "temperature": 0.4, "max_tokens": 2000})
                if r.status_code != 200:
                    raise HTTPException(502, f"DeepSeek API {r.status_code}: {r.text[:300]}")
                data = r.json()
                _track(data.get("usage", {}).get("prompt_tokens", 0), data.get("usage", {}).get("completion_tokens", 0))
                msg = data["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    return msg.get("content") or ""
                msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": await run_tool(c["function"]["name"], args)})
        return "Llegue al limite de pasos de esta conversacion. Pideme que continue si hace falta."

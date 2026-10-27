# Automatizaciones

El embudo emite eventos. n8n, ManyChat y FunnelChat los convierten en seguimiento comercial.

```
Instagram / Facebook                    Landing Agent                                   n8n
────────────────────                    ─────────────                                   ───
Comentario "EXCEL" ─► ManyChat DM ─►  /p/{slug}?utm_source=manychat&mc_id={{user_id}}
                                         │ lead ───────────────► POST webhook {type:"lead", schedule}
                      ◄── tag landing_lead (API ManyChat)             └─ 5 emails de recuperación (se cortan al comprar)
                                         ▼
                                    checkout ─► /go/{slug}/paid-main
                      ◄── tag comprador      │ ──────────────────► POST webhook {type:"purchase_main", schedule} ─► email de acceso + seguimiento
                                         ▼
                                    upsell ─► downsell ─► gracias
```

## 1. n8n: emails del embudo

```bash
./venv/bin/python automations/deploy_n8n.py
```

El script crea o actualiza, por la API de n8n, el workflow **"Landing Agent · Emails del embudo"**. Además:
- Reutiliza tu credencial de Gmail.
- Guarda la clave compartida como credencial de n8n.
- Activa el workflow.
- Escribe `N8N_WEBHOOK_URL` y `AUTOMATION_KEY` en `.env`.

Se puede ejecutar todas las veces que quieras: siempre deja el mismo resultado. Solo usa nodos nativos, sin Code, así que no depende del ejecutor de JavaScript de la instancia.

**Reparto del trabajo: la app decide, n8n programa y envía.**

```
Webhook ─► Split Out (schedule) ─► un email a la vez ─► Wait hasta send_at
        ─► GET {app}/api/automation/email ─► ¿send? ─► Gmail (HTML)
```

| Secuencia | Cuándo | Contenido |
|---|---|---|
| Recuperación | Lead que no compra: días 0 (≈1 h), 1, 3, 5 y 7 | Los 5 emails que escribe el agente. Botón al checkout con un token que identifica al lead aunque abra el email en otro dispositivo. **Se detiene sola al comprar.** |
| Acceso | Inmediato, por cada producto comprado | Botón al link real (Notion, Hotmart, Drive) o a una página de acceso de ejemplo con la marca del embudo |
| Seguimiento | 3 días después de la compra principal | "¿Cómo vas?", con los próximos pasos y soporte |

- Los emails son HTML compatible con los clientes de correo (tablas y estilos en línea) y usan los colores, las tipografías y el logo de la landing.
- Todos llevan un **link de baja**. Una baja detiene cualquier envío a ese email.
- Vista previa en el panel: **Entrega y emails → Vista previa**.
- Para una demo en vivo, `EMAIL_DAY_SECONDS=60` hace que un "día" dure un minuto.
- Requisito: n8n tiene que poder llegar a la app, porque `PUBLIC_URL` debe ser la URL pública real.

## 2. ManyChat: de un comentario a una compra

Flujo típico de infoproducto, comentario → DM → landing:

1. **Disparador**: *Instagram Comments*, con la palabra clave `EXCEL` en el post o reel del anuncio.
2. **Mensaje**: un DM con el gancho, el beneficio y un botón **"Ver la clase"**.
3. **URL del botón**: `https://TU_DOMINIO/p/{slug}?utm_source=manychat&utm_campaign=comentario-excel&mc_id={{user_id}}`
   `{{user_id}}` se inserta desde *Insert variable → Contact ID*.
4. En ManyChat, crear las etiquetas `landing_lead`, `comprador`, `comprador_upsell` y `comprador_downsell`, y el campo personalizado `landing_email` (texto).
5. Poner la API key en `.env` (*Settings → API*) como `MANYCHAT_API_KEY`.

Con eso, la app etiqueta al suscriptor en cada hito: lead, compra, upsell y downsell. En ManyChat:
- **Regla** "Tag `landing_lead` agregado" → esperar 24 h → si no tiene `comprador`, enviar recordatorio con objeción + link.
- **Regla** "Tag `comprador` agregado" → bienvenida y acceso. Excluirlo de las campañas de venta.

El origen de cada lead (`utm_source`) aparece en el panel, en el informe y en el CSV, así que se puede comparar ManyChat con Meta Ads o con tráfico orgánico.

## 3. FunnelChat (WhatsApp)

FunnelChat tiene un nodo oficial de comunidad para n8n (`n8n-nodes-funnelchat`) y webhooks firmados. Después de *Guardar lead*, en el mismo workflow:
- Crear o actualizar el contacto con el teléfono, si el formulario lo pide, y ponerle la etiqueta del producto.
- Enviar la plantilla de bienvenida aprobada por WhatsApp.

## 4. Pagos

En cada Stripe Payment Link (*After payment → Redirect*) o producto de Hotmart, la URL de éxito debe ser:

```
https://TU_DOMINIO/go/{slug}/paid-main        (producto principal)
https://TU_DOMINIO/go/{slug}/paid-upsell      (upsell)
https://TU_DOMINIO/go/{slug}/paid-downsell    (downsell)
```

El panel muestra estas URLs en **Links de pago**. Para conciliar ingresos exactos, incluidos reembolsos, conectar también el webhook de Stripe o Hotmart a n8n.

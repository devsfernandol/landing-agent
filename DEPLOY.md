# Despliegue en el VPS

**URL**: https://embudos.quepapayazo.com. El VPS usa Easypanel (Docker Swarm + Traefik).

La app corre como un **servicio de Docker Swarm**, junto a los de Easypanel, y se publica con un archivo de rutas propio para Traefik. No aparece en la interfaz de Easypanel, y Easypanel no la modifica ni la borra.

| Pieza | Dónde |
|---|---|
| Código | `/opt/landing-agent/src` |
| Imagen | `landing-agent:latest` (se construye en el VPS) |
| Servicio | `landing-agent`, en la red `easypanel`, límite de 300 MB, reinicio automático |
| Variables | `/opt/landing-agent/app.env` (permisos 600) |
| Datos | `/opt/landing-agent/data` → `/data` (SQLite, páginas e imágenes) |
| Ruta y SSL | `/etc/easypanel/traefik/config/landing-agent.yaml` (Let's Encrypt automático) |

## Actualizar

```bash
./scripts/deploy_vps.sh
```

Pasa las pruebas, sube el código, reconstruye la imagen y reinicia el servicio. Los datos no se tocan.

## Operación

```bash
docker service logs -f landing-agent                     # logs
docker service update --force landing-agent              # reiniciar
# cambiar una variable: editar /opt/landing-agent/app.env y luego:
docker service update --env-add EMAIL_DAY_SECONDS=60 landing-agent
tar czf - -C /opt/landing-agent data > backup.tgz        # copia de seguridad
```

## Demo en vivo de emails

`EMAIL_DAY_SECONDS=60` hace que un "día" de las secuencias dure un minuto. Hay que volver a `86400` después de la demo.

# API de la central

Servicio REST que recibe las alertas confirmadas del detector, calcula la
ayuda a despachar y mantiene el estado de los clientes conectados.

## Ejecución

Con dashboard (recomendado):

```bash
python -m incidentes.cli.central
```

Solo API (sin interfaz gráfica):

```bash
python -m uvicorn incidentes.server.app:app --host 0.0.0.0 --port 8000
```

Documentación interactiva: <http://localhost:8000/docs>.

## Autenticación

Si la variable `CENTRAL_API_KEY` está definida, **todas** las peticiones deben
incluir la cabecera:

```http
X-API-Key: <tu-clave>
```

Si no está definida, la API queda abierta (pensada para desarrollo local).

## Variables de entorno

| Variable | Defecto | Efecto |
|---|---|---|
| `CENTRAL_API_KEY` | *(vacío)* | Exige `X-API-Key` en cada petición. |
| `DISPATCH_WEBHOOK_URL` | *(vacío)* | POST a ese webhook al despachar una alerta. |
| `CLIENT_ALIVE_SEC` | `30` | Segundos sin latido para marcar un cliente caído. |

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Estado, número de alertas y clientes en línea. |
| `POST` | `/api/v1/alertas` | Recibe una alerta y calcula el despacho. |
| `GET` | `/api/v1/alertas` | Historial (más recientes primero). |
| `GET` | `/api/v1/alertas/{ticket}` | Detalle de una alerta. |
| `PATCH` | `/api/v1/alertas/{ticket}` | Actualiza el estado. |
| `POST` | `/api/v1/clientes/registro` | Alta de un cliente. |
| `POST` | `/api/v1/clientes/heartbeat` | Latido de vida. |
| `GET` | `/api/v1/clientes` | Clientes conectados. |
| `DELETE` | `/api/v1/clientes/{client_id}` | Baja de un cliente. |

## Enviar una alerta

```bash
curl -X POST http://localhost:8000/api/v1/alertas \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "camara-01",
    "type": "PELEA",
    "confidence": 0.87,
    "label": "CONFIRMADO IA: PELEA",
    "frame": 320,
    "time_sec": 10.6,
    "video": "C:/videos/x.mp4",
    "verified_by": "ollama/qwen2.5vl:7b",
    "reason": "forcejeo entre dos personas",
    "timestamp": "2026-10-21T10:00:00+00:00",
    "location": {"lat": -12.05, "lon": -77.04, "address": "Piso 3 - pasillo"}
  }'
```

Respuesta `201 Created`:

```json
{
  "ticket": "ALT-20261021-0001",
  "status": "registrada",
  "priority": "ALTA",
  "eta_min": 5,
  "units": ["patrulla", "supervisor"],
  "message": "Ayuda en camino: patrulla, supervisor (ETA ~5 min). Ticket ALT-20261021-0001.",
  "received_at": "2026-10-21T10:00:01+00:00"
}
```

### Prioridad y unidades

| Tipo | Prioridad | ETA | Unidades |
|---|---|---|---|
| `ARMA` | `CRITICA` | 3 min | patrulla, unidad médica, supervisor |
| `PELEA`, `ROBO`, `VIOLENCIA` | `ALTA` | 5 min | patrulla, supervisor |
| `CAIDA` | `MEDIA` | 8 min | patrulla |
| Otro | `MEDIA` | 8 min | patrulla |

Si `DISPATCH_WEBHOOK_URL` está definida, el servidor hace `POST` con el ticket,
la prioridad, las unidades y la ubicación. Si el webhook responde 2xx, la
alerta pasa a estado `despachada`.

## Consultar el historial

```bash
curl "http://localhost:8000/api/v1/alertas?limit=50"
curl http://localhost:8000/api/v1/alertas/ALT-20261021-0001
curl -X PATCH http://localhost:8000/api/v1/alertas/ALT-20261021-0001 \
  -H "Content-Type: application/json" \
  -d '{"status": "atendida"}'
```

## Clientes y heartbeat

Cada cliente se registra una vez y luego envía latidos:

```bash
curl -X POST http://localhost:8000/api/v1/clientes/registro \
  -H "Content-Type: application/json" \
  -d '{"client_id":"camara-01","address":"Av. Abancay 1234","lat":-12.05,"lon":-77.04}'

curl -X POST http://localhost:8000/api/v1/clientes/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"client_id":"camara-01"}'

curl http://localhost:8000/api/v1/clientes
```

Si un cliente no envía latidos durante `CLIENT_ALIVE_SEC`, deja de aparecer en
`GET /api/v1/clientes` (que por defecto filtra solo los que están en línea).
Un heartbeat de un cliente desconocido devuelve `404`; el cliente se
re-registra automáticamente.

## Códigos de error

| Código | Situación |
|---|---|
| `401` | `CENTRAL_API_KEY` configurada y cabecera ausente o incorrecta. |
| `404` | Ticket o cliente inexistente. |
| `422` | Cuerpo inválido (tipos, rangos de confianza o coordenadas). |

## Persistencia

La central es un **receptor en vivo**: las alertas viven en memoria y se
pierden al reiniciar. El historial está pensado para el dashboard y para
demostrar el flujo; para producción, sustituir `AlertStore` por una
implementación persistente sin cambiar el contrato REST.

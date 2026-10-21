# Arquitectura

## Principio rector

El proyecto sigue **arquitectura hexagonal** (puertos y adaptadores). La regla
de dependencia es unidireccional: las capas externas conocen a las internas,
nunca al revés.

```text
                 ┌───────────────────────────────┐
                 │        presentación           │
                 │  Tkinter · PyQt5 · FastAPI    │
                 └───────────────┬───────────────┘
                                 │
                 ┌───────────────▼───────────────┐
                 │          aplicación           │
                 │   caso de uso de 2 etapas    │
                 └───────────────┬───────────────┘
                                 │ depende de puertos
                 ┌───────────────▼───────────────┐
                 │            dominio            │
                 │  entidades + puertos (ABC)    │
                 └───────────────▲───────────────┘
                                 │ implementa
                 ┌───────────────┴───────────────┐
                 │        infraestructura        │
                 │ YOLO · OpenCV · Ollama · HTTP │
                 └───────────────────────────────┘
```

Esto permite probar el caso de uso sin cámara, sin modelo y sin red, y cambiar
cualquier adaptador sin tocar las reglas de negocio.

## Capas

| Capa | Responsabilidad | Ejemplos |
|---|---|---|
| `incidentes/domain` | Entidades inmutables, tipos de incidente y puertos (interfaces). Cero dependencias de terceros. | `FrameAnalysis`, `IncidentEvent`, `VideoReader`, `ViolenceVerifier` |
| `incidentes/application` | Orquesta el flujo: lee frames, pide sospechas, verifica, genera reporte y alerta. | `DetectIncidentsUseCase` |
| `incidentes/infrastructure` | Implementa los puertos con tecnología concreta. | `YoloIncidentDetector`, `OllamaViolenceVerifier`, `HttpAlertDispatcher` |
| `incidentes/presentation` | GUI del cliente (Tkinter) y listener de progreso. | `DetectorView`, `DetectorController` |
| `incidentes/server` | API REST de la central, modelos Pydantic, despacho, almacén y registro. | `FastAPI`, `AlertStore`, `ClientRegistry` |
| `incidentes/central` | Dashboard de la central (PyQt5 + matplotlib) y servidor embebido. | `DashboardWindow`, `RestServerThread` |
| `incidentes/cli` | *Composition roots*: conectan adaptadores y casos de uso. | `client`, `central`, `deploy`, `diagnose`, `smoke_test` |

## Flujo de una detección

```text
Operador          Controller            Caso de uso              Worker IA           Central
   │                  │                     │                       │                  │
   │ Iniciar          │                     │                       │                  │
   ├─────────────────▶│  hilo de fondo      │                       │                  │
   │                  ├────────────────────▶│                       │                  │
   │                  │                     │ lee frame             │                  │
   │                  │                     │ YOLO + heurística     │                  │
   │                  │◀────────────────────┤ on_progress           │                  │
   │                  │                     │ ¿sospecha?            │                  │
   │                  │                     ├──────────────────────▶│ verify(jpeg)     │
   │                  │                     │ (cola acotada)        │ Ollama           │
   │                  │                     │                       │ veredicto        │
   │                  │                     │◀──────────────────────┤                  │
   │                  │◀────────────────────┤ on_incident_alert     │                  │
   │  alarma          │                     │                       │                  │
   │                  │                     ├─────────────────────────────────────────▶│ POST alerta
   │                  │◀────────────────────┤ on_alert_dispatched   │                  │
   │ "Ayuda en camino"│                     │                       │                  │
```

1. El controller lanza el procesamiento en un hilo para no bloquear Tkinter.
2. El caso de uso lee el video frame a frame y consulta al detector.
3. Si hay sospecha y ha pasado el *cooldown*, encola un JPEG para el worker.
4. El worker verifica con Ollama y, solo si el veredicto es positivo, crea un
   `IncidentEvent`, notifica a la UI y lanza el envío a la central en otro hilo.
5. Al terminar el video se esperan las verificaciones pendientes, se escribe el
   reporte JSON y se notifica el resumen.

## Concurrencia

| Hilo | Función |
|---|---|
| Hilo principal de Tkinter | Widgets y bucle de eventos. Toda actualización pasa por `root.after`. |
| `detector-worker` | Lee el video y ejecuta la etapa 1. |
| `ai-verifier` | Consume la cola de sospechas y llama a Ollama. |
| `alert-dispatch` | Envía la alerta a la central sin frenar la verificación. |
| `client-beacon` | Registro y heartbeats periódicos. |
| Hilo principal de PyQt5 | Dashboard; sondea `store` y `registry` con `QTimer`. |
| `central-rest` | uvicorn atiende la API REST. |

Estados compartidos:

- La cola de verificación está **acotada** (`JOB_QUEUE_SIZE = 2`). Si la IA no
  da abasto, se descarta la sospecha antes que crecer sin límite.
- `pending` y `alert_sent` se protegen con un `Lock` en el caso de uso.
- `AlertStore` y `ClientRegistry` usan un `Lock` propio; la GUI solo lee.
- El dashboard **nunca** toca la red desde el hilo de Qt: el servidor corre
  aparte y el estado se consulta en memoria.

## Manejo de errores

| Punto | Política | Motivo |
|---|---|---|
| Verificador (Ollama) | **Fail-closed**: error/timeout → `violent=False` | Evitar alarmas falsas por una respuesta incompleta. |
| Envío a la central | **Fail-open**: error/timeout → `delivered=False` | La detección local debe seguir aunque la red falle. |
| Parser del veredicto | JSON inválido → `NADA` | El modelo puede devolver texto extra; nunca se interpreta como confirmación. |
| Productor de video | `OSError` con mensaje claro | El usuario ve el error en la GUI. |
| Webhook de despacho | Best-effort, nunca lanza | El plan queda en el log y en el historial. |
| GUI | `try/except` por ciclo de sondeo | Un fallo visual no rompe el bucle de eventos. |

## Decisiones de diseño

- **Entidades inmutables** (`frozen dataclasses`): evitan efectos colaterales
  entre hilos y facilitan las pruebas.
- **Funciones de imagen inyectadas**: `snapshot_encoder` y `pending_marker` se
  pasan al caso de uso desde el *composition root*, de modo que `application`
  no importa OpenCV.
- **Configuración inmutable y por entorno**: `AppConfig.from_env()` +
  `dataclasses.replace` para los argumentos CLI; no hay estado global mutable.
- **Central sin persistencia**: el almacén es en memoria a propósito. Cambiar
  a SQLite/PostgreSQL implica reemplazar `AlertStore`, sin tocar endpoints.
- **Dashboard por sondeo**: `QTimer` lee el estado compartido cada 800 ms. Es
  más simple y seguro que notificar widgets desde el hilo de red.
- **Puertos con contrato explícito**: `ViolenceVerifier` documenta fail-closed;
  `AlertDispatcher`, fail-open. Los adaptadores cumplen ese contrato.

## Puntos de extensión

| Necesidad | Dónde cambiar |
|---|---|
| Cámara RTSP en vivo | Nuevo `VideoReader` + inyectarlo en `cli/client.py`. |
| Modelo propio de violencia | Colocar `violence.pt`; `YoloIncidentDetector` lo detecta. |
| Otra IA de verificación | Nueva clase que implemente `ViolenceVerifier`. |
| Persistir alertas | Nueva implementación de `AlertStore` con la misma interfaz. |
| Otro canal de despacho | Sustituir `notify_units` o añadir un adaptador de notificación. |
| Otra UI | Implementar `ProgressListener` y reutilizar el caso de uso. |

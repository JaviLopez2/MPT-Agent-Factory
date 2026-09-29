# Validación y auditoría — 2026-09-29

## Fuente y alcance

Fuente principal: `AGENT_FACTORY_STATE.md` de `JaviLopez2/CustomVideoGenerator`,
rama `moneyprinter_qwen21_quality_v3_1`, commit
`6d27ba4963ffe469d635db71eaeec506a8ff4b61`.
Se contrastó con la CLI, `VideoParams`, pipeline `task.start`, configuración,
storage, publicación, referencias, diagnostics y rutas del bridge actuales.

El baseline **126 passed, 2 skipped, 20 subtests passed, ~50.67 s** pertenece al
core y fue comunicado por el usuario. No se ha vuelto a ejecutar ni se atribuye
a este proyecto. La auditoría no valida de nuevo la calidad V3.2.1 ni el benchmark SX-70.

Dos precisiones sobre el documento original:

- `continuity` existe como metadato del planner/cadena; los roles de referencias
  manuales aceptados por el código son `identity/detail/internal/context/other`.
- El bridge auditado solo expone `/v1/images/*`. Un 404 en su raíz sirve como
  liveness HTTP; no se inventó un endpoint de readiness.

La interfaz headless es la llamada a `app.services.task.start`, en un subproceso
aislado con el intérprete MPT. Se evita el flujo de publicación de la CLI y se
fuerza publicación desactivada en el wrapper. No se modificaron archivos de MPT.

## Regresiones demostradas y correcciones

| Hallazgo | Corrección | Evidencia |
| --- | --- | --- |
| PID visible distinto del PID que ve psutil en `/proc` | Conservada y comprobada la traducción Linux `NSpid`/namespace | Worker real y reinicio de supervisor |
| Tolerancia de 0,1 s confundía identidades distintas | Igualdad exacta de fecha de creación | Regresión de identidades próximas |
| Reciclado entre `alive()` y consulta posterior | Segunda comprobación antes de recorrer/señalar el árbol | Test que cambia la identidad entre ambas consultas |
| `wait_procs` podía esperar el PID del namespace exterior | Espera por identidad/estado, zombie terminado, reap usando PID visible | Servicio HTTP real: arranque, recuperación de propiedad y parada |
| Fecha inventada si el worker terminaba demasiado pronto | Identidad opcional; recuperación por argv y resultado duradero | Contrato `VideoAgent` admite `None` |
| Resolver symlink del Python de un venv perdía sus dependencias | Mantener la ruta del ejecutable, sin resolver enlaces | Regresión de configuración |
| Diagnostics con `plan_scenes=null` bloqueaba evaluación | Resultado técnico fallido con `Malformed plan_scenes` | Regresión de diagnostics malformado |

La prueba de recuperación inicia un supervisor real y un worker que crea un
subproceso hijo. Mata el supervisor, verifica que worker/hijo siguen vivos,
elimina la identidad persistida para simular la ventana Popen/SQLite y reinicia
con una cancelación pendiente. Verifica estado `cancelled`, ambos procesos
terminados y un único evento `worker_started`.

## Resultados de tests realmente observados

| Ejecución | Resultado |
| --- | --- |
| Sesión anterior, antes de adaptación inicial PID | 17 passed, 6 failed in 6.41 s |
| Sesión anterior, después de adaptación inicial PID | 23 passed in 22.38 s |
| Regresiones enfocadas añadidas al retomar | Dos fallos reproducidos antes de corregir identidad |
| Regresiones enfocadas después de corregir | 3 passed in 0.42 s |
| Suite con el test nuevo de servicio HTTP | 28 passed, 1 failed in 21.36 s |
| Corrección de espera/cancelación de procesos | 4 passed in 0.59 s |
| Última suite completa Factory | **29 passed in 3.56 s** |

Último comando completo ejecutado:

```bash
.venv/bin/python -m compileall -q src
.venv/bin/python -m pytest -q
```

Ambos finalizaron con código 0. Los tests del wrapper emplean un fixture de MPT
que genera un MP4 con FFmpeg y diagnostics sintéticos. Incluyen cola, transiciones,
locks, errores, timeout, cancelación, recuperación, worktrees, protección de
fuentes y dashboard AppTest. No demuestran que Qwen genere imágenes correctas.
Después de esta ejecución solo se ajustó la anotación de retorno de `VideoAgent`
y se añadieron documentación/evidencias; no se atribuyen resultados a tests futuros.

## Vertical slice con MPT real

Se creó un worktree nuevo, `factory/validation-v01`, en el commit auditado. La
configuración de esa prueba estaba fuera del repo Factory y no incluía servicios:
se utilizó `video_source=local`, imagen de prueba, `no-voice`, sin subtítulos ni BGM.
El wrapper llamó al pipeline real de CustomVideoGenerator, sin fixture.

| Dato observado | Valor |
| --- | --- |
| Job | `1ff95dbf-db90-4a4d-a98b-9a5bef931147` |
| Estado persistido | `succeeded`, progreso 100, error null |
| Secuencia registrada | created → queued → preparing → running → collecting → evaluating → succeeded |
| Artifacts registrados | 14 |
| MP4 final | `final-1.mp4`, 27.320 bytes |
| ffprobe | 1080×1920, vídeo y audio, 3,030 s |
| Evaluación | technical_pass=true; failures=[]; visual_quality=not_evaluated |
| Diagnostics precision | Ausentes, como corresponde a un job local |

Los artifacts incluyen PNG, clip local, audio, vídeo combinado/final, script,
progreso, resultado, provenance y logs. La copia de evidencia está en
`validation/local-smoke.mp4`; el extracto portátil está en
`validation/local-smoke-result.json`. No se empaquetó el runtime, SQLite ni
configuraciones privadas; los originales siguen en el workspace de validación.

Se ejecutó `doctor --probe-mpt`: importación MPT correcta, commit correcto y
ffprobe 6.1.1. `services=[]` describe esta prueba offline; no son health checks
de los servicios del PC del usuario.

## Dashboard real

Se arrancó Streamlit en `127.0.0.1:8600` y se consultó su endpoint de salud en el
mismo entorno de red del proceso:

```text
DASHBOARD_HTTP 200 ok
EXCEPTIONS []
```

AppTest abrió la DB del job real: cola=0, activos=0, completados=1, fallidos=0,
UUID seleccionado correcto y un reproductor de vídeo (`VIDEOS 1`). El servidor
se detuvo al terminar la comprobación. Una comprobación previa entre herramientas
en namespaces de red distintos recibió connection refused; se repitió correctamente
con servidor y cliente en el mismo namespace. No se necesitó automatización de navegador.

## Entorno y dependencias

Validación en Linux, Python 3.12.14, `.venv` del proyecto. Versiones observadas:

```text
mpt-agent-factory 0.1.0 editable
psutil 7.2.2
streamlit 1.64.0
pytest 9.1.1
loguru 0.7.3
moviepy 2.2.1
edge-tts 7.2.7
openai 2.24.0
ffmpeg/ffprobe 6.1.1 (sistema)
```

Las tres últimas dependencias Python se instalaron solo para ejecutar MPT real
en esta validación. No se añadió MoviePy/Edge TTS/OpenAI a los requisitos runtime
de Factory. `pyproject.toml` separa dependencias de Factory y dev; en Windows
MPT conserva su propio Python portable y sus paquetes. No se ejecutó `uv sync`
del core ni se instaló nada en el PC del usuario.

## Estado Git y límites pendientes

- Factory tiene Git local, rama `main`, sin remoto. `f4dbfad` conserva el checkpoint
  recibido antes de las correcciones; la entrega añade un commit posterior.
- `mpt-source`, rama estable y commit auditado: sin diferencias rastreadas ni
  archivos nuevos. No se actualizó su documento de estado porque se mantuvo intacto.
- El worktree previo `mpt-integration-source` tenía al retomar una modificación de
  `resource/fonts/STHeitiMedium.ttc`. Se detectó la discrepancia con el handoff viejo
  y se conservó; no se usó como base de la validación nueva.
- El worktree nuevo `mpt-validation-source` se mantiene limpio. Su font apareció
  truncado en el checkout y se restauró allí desde HEAD antes de generar; no se
  alteró el worktree previo ni el stable. Su `config.toml` local está ignorado.

Pendiente: ejecutar regresiones en Windows nativo, smoke local en el Python portable,
generación Qwen/ComfyUI con diagnostics reales, benchmark SX-70 y prueba prolongada
de cola. La rama Windows usa psutil directamente; no se presenta como verificada
en Windows por haber pasado los tests Linux. Tampoco se han validado la calidad
visual, auto-mejora, publicación ni promoción: esas funciones no están implementadas.

Orden recomendado en el PC: `doctor --probe-mpt` → tests de recuperación/servicios
→ smoke local → dashboard → `examples/first-job.json` con el stack habitual →
benchmark Polaroid con las seis referencias reales. Los comandos están en README.

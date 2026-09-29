# MPT Agent Factory v0.1 — handoff

> **Actualización al retomar, 2026-09-29:** el contenido histórico que sigue se
> conserva para trazabilidad. El estado vigente está en `README.md` y
> `docs/VALIDATION.md`. Factory ya tiene Git local (checkpoint `f4dbfad`); se han
> corregido las regresiones demostradas de identidad/espera de PID, completado
> la prueba de recuperación con cancelación del árbol y generado un MP4 con MPT
> real usando material local. Última suite Factory: **29 passed in 3.56 s**.
> Dashboard 8600: HTTP 200 y un vídeo del job real mostrado por AppTest sin
> excepciones. Windows nativo y Qwen/ComfyUI siguen pendientes de prueba.
> Se detectó y conservó una modificación preexistente del font en el worktree
> antiguo de integración; stable sigue limpio. Consultar el documento de
> validación para los detalles y no usar las limitaciones históricas como si
> fueran el estado actual.

Estado congelado para retomar el trabajo con Astra. Fecha de este handoff: 2026-09-29.

## Estado vigente al cierre de la vertical slice

La última ejecución de `compileall` y pytest fue:

```text
.venv/bin/python -m compileall -q src && .venv/bin/python -m pytest -q
.............................                                            [100%]
29 passed in 3.56s
```

El dashboard separado en `127.0.0.1:8600` devolvió `HTTP 200 ok`; AppTest abrió la
DB del job MPT real, mostró el vídeo y no registró excepciones. La generación real
usó material local y terminó `succeeded` con MP4 1080×1920 de 3,030 s. El detalle
reproducible está en `TEST_REPORT.md` y `docs/VALIDATION.md`.

El problema de cancelación pendiente es específico de workflows ya enviados a
ComfyUI: Factory puede terminar el worker local y conserva el estado, pero no envía
un `/interrupt` global al ComfyUI compartido. El siguiente job de imágenes espera
la cola libre según el preflight. Tampoco están validados Windows nativo, los cuatro
servicios reales ni una generación Qwen con diagnostics reales.

### Limitaciones específicas del entorno Work

La validación se hizo en Linux, Python 3.12.14, con namespace PID anidado. La
traducción `NSpid` está cubierta por tests Linux; la rama Windows de psutil no se
ha ejecutado. Procesos de llamadas de herramienta separadas pueden estar en
namespaces de red distintos: una prueba cruzada de 8600 falló por `connection
refused`, y la prueba válida se repitió con cliente y servidor en el mismo proceso.
El Work no tenía acceso a tus procesos, modelos, tokens ni rutas Windows, por lo
que la prueba Qwen/ComfyUI queda para tu PC. No se ha creado un remoto GitHub; el
proyecto se entrega como repo Git local y bundle dentro del ZIP.

### Estado Git al cierre

Factory contiene todos los cambios válidos de esta sesión en su rama local `main`.
`mpt-source`, rama `moneyprinter_qwen21_quality_v3_1`, commit
`6d27ba4963ffe469d635db71eaeec506a8ff4b61`, permanece limpio. El worktree antiguo
`mpt-integration-source` conserva la modificación preexistente de
`resource/fonts/STHeitiMedium.ttc`; no se revirtió. El worktree nuevo de validación
se dejó limpio tras restaurar su font desde HEAD. El ZIP excluye secretos, `factory.toml`,
SQLite, logs activos, `.venv` y caches.

## Alcance de esta sesión

Se leyó `AGENT_FACTORY_STATE.md` de `JaviLopez2/CustomVideoGenerator`, rama
`moneyprinter_qwen21_quality_v3_1`, y se contrastó con el código de esa revisión.
El commit inspeccionado es:

```text
6d27ba4963ffe469d635db71eaeec506a8ff4b61
Add Agent Factory project state and autonomous workflow source of truth
```

El baseline declarado por el usuario y documentado en `AGENT_FACTORY_STATE.md` sigue siendo:

```text
126 passed
2 skipped
20 subtests passed
~50.67 seconds
```

Ese baseline del core no se volvió a ejecutar durante esta sesión.

## Árboles y Git

### Core MPT estable

Clone local usado para inspección:

```text
/workspace/scratch/845a021772d1/mpt-source
```

Estado real:

```text
branch: moneyprinter_qwen21_quality_v3_1
remote: origin -> https://github.com/JaviLopez2/CustomVideoGenerator.git
HEAD: 6d27ba4963ffe469d635db71eaeec506a8ff4b61
git status: limpio
```

No se modificó este árbol. No hay `config.toml` local en este clone.

### Worktree de integración utilizado para el smoke check

```text
/workspace/scratch/845a021772d1/mpt-integration-source
branch: factory/integration-local
HEAD: 6d27ba4963ffe469d635db71eaeec506a8ff4b61
```

`git status --porcelain` no muestra cambios rastreados. `git status --ignored`
muestra únicamente `!! config.toml`; es una copia local ignorada creada para que el
probe de importación pueda cargar la configuración de MPT.

### Proyecto Factory

```text
/workspace/scratch/845a021772d1/MPT-Agent-Factory
```

Es un repositorio Git local en la rama `main`. El checkpoint recibido era `f4dbfad`;
el SHA exacto se registra en la respuesta de cierre y puede comprobarse con `git rev-parse HEAD`. No hay remoto
de Factory.

Fuera del proyecto se generaron para la comprobación local:

```text
/workspace/scratch/845a021772d1/integration.toml
/workspace/scratch/845a021772d1/integration-data/factory.sqlite3
```

## Archivos creados en Factory

```text
.gitignore
pyproject.toml
factory.example.toml
examples/first-job.json
examples/polaroid-job.json
scripts/setup.ps1
scripts/start-dashboard.ps1
scripts/start-supervisor.ps1
src/mpt_factory/__init__.py
src/mpt_factory/__main__.py
src/mpt_factory/agents.py
src/mpt_factory/artifacts.py
src/mpt_factory/cli.py
src/mpt_factory/common.py
src/mpt_factory/config.py
src/mpt_factory/dashboard.py
src/mpt_factory/db.py
src/mpt_factory/jobs.py
src/mpt_factory/processes.py
src/mpt_factory/runner.py
src/mpt_factory/services.py
src/mpt_factory/supervisor.py
src/mpt_factory/worktrees.py
tests/conftest.py
tests/test_factory.py
WORK_HANDOFF.md
```

La instalación editable también generó archivos ignorados bajo
`src/mpt_agent_factory.egg-info/`; las ejecuciones de Python generaron
`__pycache__` y `.pytest_cache`.

No se modificó ningún archivo del core `mpt-source` ni del commit estable remoto.

## Arquitectura implementada hasta el corte

- `db.py`: SQLite WAL, tablas `jobs`, `events`, `artifacts`, `experiments` y
  `service_processes`; transiciones persistentes y reclamación atómica de un único job.
- `supervisor.py`: supervisor determinista con estados `created -> queued -> preparing ->
  running -> collecting -> evaluating -> succeeded/failed`, backoff de preflight, heartbeat
  y recuperación por `request.json`.
- `runner.py`: proceso separado lanzado con el intérprete configurado de MPT; carga
  `app.services.task.start(...)`, fuerza `upload_post_auto_upload=false`, desactiva Redis,
  usa storage por intento, escribe progreso/resultado/provenance y bloquea escritura sobre
  los árboles fuente mediante audit hook.
- `services.py`: health check HTTP local, control opcional de procesos configurados y
  comprobación de cola `/queue` de ComfyUI. Las URLs de servicio deben ser locales.
- `worktrees.py`: exige que el stable esté en la rama configurada y sin cambios rastreados;
  crea worktrees `factory/...` fuera del stable. No hay merge ni promoción automática.
- `artifacts.py`: recoge MP4, imágenes, diagnostics y logs con tamaño/hash; valida MP4 con
  `ffprobe` y el schema 3 de `precision_diagnostics.json`. La calidad visual queda marcada
  como `not_evaluated` y requiere revisión humana.
- `agents.py`: contratos `VideoAgent`, `EvaluatorAgent`, `EngineerAgent` y evaluator técnico.
  No hay LLM de auto-mejora.
- `dashboard.py`: dashboard Streamlit separado; el comando está preparado para el puerto
  8600 y muestra jobs, progreso, artifacts, diagnostics y vídeo.
- `cli.py`: `init`, `doctor`, `create`, `enqueue`, `show`, `list`, `cancel`, `retry`,
  `supervisor`, `services`, `experiment` y `dashboard`.

Los puertos previstos en `factory.example.toml` son 8080, 8090, 8188 y 8501; los comandos
de autoarranque están vacíos salvo el ejemplo de la WebUI. El dashboard Factory usa 8600.

## Pruebas ejecutadas y resultados reales

### Suite Factory, primera ejecución

Comando:

```text
python -m pytest -q
```

Resultado real antes del intento de adaptación de PID:

```text
17 passed, 6 failed in 6.41s
```

Fallos observados:

- `alive()` devolvió `False` para un worker recién lanzado, con PID registrado `103`.
- El timeout recibió `GPU lease held by another Factory worker/check`.
- La prueba de recolección no tenía `attempt_dir` porque el preflight había sido diferido.
- `test_pid_reuse_identity_guard` no pudo inspeccionar el propio PID (`psutil.NoSuchProcess`,
  PID visible `2`).
- Dashboard y unknown-param fallaron de forma derivada por el bloqueo de GPU.

### Suite Factory, segunda ejecución

Después de añadir al archivo ya existente `src/mpt_factory/processes.py` una rama de
traducción para `/proc`/PID namespace Linux, sin tocar MPT, se volvió a ejecutar exactamente
el mismo comando:

```text
23 passed in 22.38s
```

Esto solo valida el entorno Linux actual y el fixture de pruebas. No valida aún el
comportamiento de procesos en Windows ni demuestra que la traducción sea la solución final.
No se debe interpretar como una validación del baseline 126/2/20 del core.

Las pruebas Factory usan un MPT fixture temporal que crea un MP4 real con FFmpeg, una imagen
y un diagnostics JSON sintético. Por tanto, la vertical slice quedó probada contra el contrato
del wrapper, SQLite, estados, locks, artifacts y dashboard embebido, pero todavía no contra
una generación completa de `CustomVideoGenerator` con Qwen/ComfyUI.

### Smoke check de importación MPT

Comando ejecutado sobre `mpt-integration-source`:

```text
python -m mpt_factory --config /workspace/scratch/845a021772d1/integration.toml doctor --probe-mpt
```

Resultado real relevante:

```json
{
  "python_exists": true,
  "mpt_exists": true,
  "config_exists": true,
  "services": [],
  "mpt_commit": "6d27ba4963ffe469d635db71eaeec506a8ff4b61",
  "ffprobe": "ffprobe version 6.1.1-3ubuntu5 ...",
  "mpt_import_ok": true
}
```

`services: []` se debe a que el `integration.toml` temporal no definía bloques
`[[services]]`; no es un health check real de 8080/8090/8188/8501.

No se ejecutó una generación real de vídeo con el MPT actual, no se recogió un MP4 real de
Qwen/ComfyUI y no se validó Streamlit escuchando realmente en 8600.

## PID: estado actual y problema pendiente

El entorno de ejecución usado para las pruebas tiene PID namespace anidado. Una observación
real fue:

```text
os.getpid() -> 5
/proc/self -> 26267
NSpid -> 26267 5
```

`psutil` puede observar el PID del namespace exterior mientras el supervisor guarda el PID
visible del namespace interior. Eso produjo falsos `not alive`, impidió localizar el propio
proceso y bloqueó pruebas posteriores por el lock de GPU.

El archivo actual contiene un intento de adaptación Linux que compara `/proc/self`, lee
`NSpid` y convierte el PID visible antes de llamar a `psutil`/`os.kill`. Ese intento se añadió
antes de este handoff y es la razón por la que la segunda ejecución dio 23/23. Sigue pendiente
una validación real en Windows, y no se debe dar por resuelto todavía. Durante este handoff no
se ha hecho ninguna modificación adicional para arreglarlo.

## Dependencias instaladas en el entorno de esta sesión

El proyecto se instaló editablemente en el Python del runtime actual, no en un `.venv` de
Windows:

```text
Python: /opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python
Python version: 3.12 runtime
mpt-agent-factory 0.1.0 (editable)
pytest 9.1.1
psutil 7.2.2
streamlit 1.64.0
moviepy 2.2.1
loguru 0.7.3
edge-tts 7.2.7
openai 2.24.0
```

También estaban presentes dependencias generales del runtime, como `pydantic 2.13.5`,
`requests 2.34.2`, `pillow 11.3.0`, `uvicorn 0.54.0` y `ffmpeg/ffprobe` del sistema.
No se creó el `.venv` que usaría `scripts/setup.ps1` en Windows y no se ejecutó `uv sync`
del core MPT.

## Qué se estaba haciendo al agotarse Astra

La implementación de infraestructura ya estaba escrita y se estaba validando la primera
vertical slice. El último trabajo fue:

1. diagnosticar el fallo de PID de la primera suite;
2. ejecutar de nuevo la suite tras el intento de traducción de PID;
3. ejecutar `doctor --probe-mpt` contra un worktree de MPT en el mismo commit estable;
4. verificar que la importación de `app.services.task` funciona.

El siguiente paso pendiente era probar el wrapper contra una generación MPT real con la
configuración/servicios locales del usuario, empezando por un job barato y con publicación
desactivada. Esa prueba no se ejecutó antes del corte.

## Siguientes pasos concretos para Astra

1. No tocar este handoff ni el core estable hasta revisar el estado y elegir si
   `MPT-Agent-Factory` se convierte en un repositorio separado.
2. En Windows, crear el `.venv` con `scripts/setup.ps1`, copiar `factory.example.toml` a
   `factory.toml` y ajustar las rutas absolutas del MPT portable, Python y worktrees.
3. Resolver el diseño de PID/proceso en Windows con una prueba pequeña de `Popen`, `psutil`,
   `create_time`, cancelación y reinicio del supervisor. No asumir que la rama Linux del
   archivo actual aplica a Windows.
4. Añadir los cuatro bloques `[[services]]` con las URLs/launchers reales del usuario y
   ejecutar `doctor`/`services status`. El bloque del bridge debe respetar que el bridge
   auditado solo expone `/v1/images/*`; su raíz 404 solo acredita liveness HTTP.
5. Ejecutar primero `examples/first-job.json` con `video_source=openai_image` y una sola
   generación. Confirmar en SQLite la secuencia de estados, el MP4, las imágenes y
   `precision_diagnostics.json` reales.
6. Comparar `mpt-source` antes/después: rama, `git status`, commit, `config.toml` y ningún
   archivo generado en el árbol estable.
7. Probar `python -m mpt_factory dashboard --port 8600` y revisar el vídeo, diagnostics y
   eventos desde el dashboard.
8. Solo después de esa prueba, considerar registrar Factory como repositorio separado y
   añadir más evaluación. Auto-merge, publicación y auto-mejora LLM siguen fuera de v0.1.

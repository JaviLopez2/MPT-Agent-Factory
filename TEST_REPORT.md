# Test report — MPT Agent Factory v0.1

## Última corrección validada — 2026-09-30, guion controlado y timeline MPT

Factory, worktree factory-polaroid-fix sobre origin/main f00fb33:

```text
PYTHONPATH=src /workspace/scratch/845a021772d1/MPT-Agent-Factory/.venv/bin/python -m pytest -q
50 passed in 8.40s

PYTHONPATH=src /workspace/scratch/845a021772d1/MPT-Agent-Factory/.venv/bin/python -m compileall -q src scripts tests
exit 0
git diff --check
exit 0
```

La pasada completa anterior de esta sesión fue 50 passed in 8.23s. Se añaden
seis casos: texto literal completo, preservación mediante el helper real/SQLite/
snapshot, y rechazo de cuatro formas de script ausente/inválido. No se inicia
worker alguno en esos tests; los archivos de referencias son fixtures inertes.
La primera pasada enfocada produjo 5 failed, 4 passed in 0.39s por un método de
DB incorrecto en el test nuevo; se corrigió el test, no el API de producción.

MPT, worktree separado mpt-narration-fix, rama factory/narration-timeline:

```text
MPT_RUN_INTEGRATION_TESTS=0 /workspace/scratch/845a021772d1/MPT-Agent-Factory/.venv/bin/python -m pytest -q test/services/test_narration_timeline.py test/services/test_task.py test/services/test_qwen_quality_v31.py test/services/test_voice.py test/services/test_subtitle.py
173 passed, 6 skipped, 1 warning, 20 subtests passed in 5.14s

/workspace/scratch/845a021772d1/MPT-Agent-Factory/.venv/bin/python -m compileall -q app test/services/test_narration_timeline.py
exit 0
```

Es una suite ampliada de cinco archivos, no toda la suite MPT. Incluye once casos
nuevos de timeline, ambas representaciones de SubMaker, subtítulos visibles,
paso a renderer, presupuesto 9, planner estructurado y Precision mock, fallback
sin alineación y ausencia de Whisper. Los tests no prueban calidad generativa.

Historial de intentos MPT de esta sesión (sin ocultar fallos):

- Primera regresión: 2 failed, 7 passed in 1.39s; faltaba video_subject en dos
  fixtures de VideoParams. Corregido en tests.
- Suite ampliada: error de colección por pydub ausente.
- Subconjunto sin test_voice: 112 passed, 3 skipped, 18 subtests passed in 3.16s.
- Con pydub: 1 failed, 172 passed, 6 skipped, 1 warning, 20 subtests passed in
  3.11s; google.genai ausente en el entorno. Instalado y repetido con resultado
  final limpio arriba. El warning restante es audioop deprecated de pydub.

Dependencias instaladas para los tests: pydub==0.25.1 y google-genai==2.11.0,
ya declaradas por MPT, con sus transitivas. Ningún pyproject/lock se modifica.
Linux Work/Python 3.12.14; no constituye una pasada nativa Windows.

Polaroid SÍ se ejecutó anteriormente en el PC: job
c3ac5e07-4ad2-4a65-bdfa-97cbba9994a8, succeeded/technical_pass=true,
52.51 s, 1080x1920, 11 imágenes, 41 artifacts, pero precision_scenes=0 y refs=0.
No sirve como validación del benchmark controlado. El retraso de colección
corresponde al supervisor ausente, confirmado por el usuario; no se inventa otra
causa. No se ha repetido Polaroid ni llamado Qwen/ComfyUI durante esta corrección.

Los apartados siguientes son históricos; no sustituyen este estado vigente.

## Consolidación Windows y revisión actual — 2026-09-30

El usuario confirmó una ejecución nativa Windows posterior al fix de
`worker.json`: **29 tests passed**. Esa duración y su salida completa no están
disponibles en este entorno Work, así que no se inventa una cifra.

La revisión actual añade el handshake y las regresiones de
`tests/test_windows_worker.py`, más validaciones estáticas del benchmark. Ejecutada
en el entorno Linux de Work:

```text
.venv/bin/python -m compileall -q src scripts
.venv/bin/python -m pytest -q
....................................                             [100%]
44 passed in 5.28s
```

La prueba cubre fixtures y procesos locales; no equivale a otra ejecución Windows.
El benchmark Polaroid/SX-70 no se ha ejecutado desde Factory.

La evidencia de jobs Windows aportada por el usuario está en
`validation/windows-confirmed.json`. El bosque produjo `precision_scenes=0`, por
lo que la ruta Precision con referencias sigue pendiente de validar.

Fecha: 2026-09-29. Este documento registra la última ejecución realizada en el
entorno de trabajo; no es una predicción para Windows ni para el stack Qwen del PC.

## Última suite Factory

Directorio: `/workspace/scratch/845a021772d1/MPT-Agent-Factory`

```text
.venv/bin/python -m compileall -q src && .venv/bin/python -m pytest -q
```

Resultado exacto:

```text
.............................                                            [100%]
29 passed in 3.56s
```

Código de salida: `0`. `compileall` tampoco produjo salida ni error.

Los 29 tests cubren SQLite y transiciones, cola/prioridad, snapshots de entradas,
locks, fallos y timeouts, protección de escritura del árbol MPT, artifacts y
diagnostics malformados, worktrees, recuperación tras reinicio del supervisor,
cancelación del árbol PID, gestión de un servicio HTTP real y el dashboard mediante
AppTest. Los fixtures de pipeline crean MP4 con FFmpeg y diagnostics sintéticos.

## Evidencia MPT real

Job `1ff95dbf-db90-4a4d-a98b-9a5bef931147`, ejecutado contra el commit MPT
`6d27ba4963ffe469d635db71eaeec506a8ff4b61` en un worktree de validación limpio.
Usó una imagen local de prueba, `video_source=local`, sin voz, subtítulos, BGM ni
servicios externos. El pipeline real completó la secuencia:

```text
created → queued → preparing → running → collecting → evaluating → succeeded
```

El resultado persistido fue `succeeded`, progreso `100`, error `null`, 14 artifacts,
y un MP4 de 27.320 bytes. `ffprobe` observó vídeo 1080×1920, audio y duración
3,030 s. La evaluación fue `technical_pass=true`, sin fallos ni warnings,
`visual_quality=not_evaluated` y `human_review_required=true`. Al ser un job local,
no debía generar `precision_diagnostics.json`; no se cuenta como prueba Qwen.

La evidencia pequeña se conserva en `validation/local-smoke.mp4` y
`validation/local-smoke-result.json`.

## Dashboard

Se inició el dashboard separado en `127.0.0.1:8600` sobre esa misma base de datos.
La comprobación realizada en el mismo namespace de red devolvió:

```text
DASHBOARD_HTTP 200 ok
EXCEPTIONS []
```

AppTest mostró el job real, métricas cola=0/activos=0/completados=1/fallidos=0 y un
vídeo (`VIDEOS 1`). El proceso se detuvo al finalizar la prueba.

## Alcance que sigue sin validarse

- Windows nativo, incluido el comportamiento de PID con psutil en ese sistema.
- Arranque y salud de las instancias reales 8080/8090/8188/8501.
- Generación Qwen/ComfyUI, imágenes generadas y `precision_diagnostics.json` real.
- Benchmark Polaroid SX-70, evaluación visual/factual, ejecución de varias horas y
  retención de artifacts.
- Cancelación de un workflow que ya fue enviado a ComfyUI: Factory puede cancelar
  su worker local, pero no envía un `/interrupt` global al servidor compartido.

## Limitaciones del entorno Work

La ejecución se hizo en Linux con Python 3.12.14 y un namespace de PID anidado;
eso obligó a traducir `NSpid` para observar y señalizar procesos. La rama Windows
usa psutil directamente y no se ha probado en una máquina Windows.

Los procesos lanzados por llamadas de herramienta distintas pueden quedar en
namespaces de red distintos. Una prueba HTTP cruzada recibió `connection refused`;
la comprobación válida de 8600 se repitió con servidor y cliente dentro del mismo
proceso de prueba. No se usó navegador para esa validación.

El entorno Work no dispone de las instancias reales del stack del usuario ni de
sus modelos, tokens o rutas Windows. Por eso el ZIP no contiene `factory.toml`,
SQLite, logs de ejecución, `.venv`, claves ni API keys; solo incluye
`factory.example.toml` y la evidencia local no sensible.

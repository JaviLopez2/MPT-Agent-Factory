# Test report — MPT Agent Factory v0.1

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

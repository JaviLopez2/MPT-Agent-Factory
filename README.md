# MPT Agent Factory v0.1

Infraestructura local para poner generaciones MPT en cola, ejecutarlas sin WebUI,
recuperar su estado y revisar sus resultados. Proyecto separado de
`JaviLopez2/CustomVideoGenerator`; no incorpora ni modifica su código.

La primera vertical slice está ejecutada contra MPT real con un material local:
SQLite → worker MPT → MP4 → evaluación técnica → dashboard. El bosque Qwen/ComfyUI
y el handshake Windows ya fueron validados desde el PC del usuario. La ruta
Precision con referencias SX-70 sigue pendiente. Evidencias y límites:
[VALIDATION.md](docs/VALIDATION.md).

## Arquitectura

| Componente | Responsabilidad |
| --- | --- |
| `db.py`, `jobs.py` | SQLite WAL, cola por prioridad/FIFO, transición atómica, snapshots de entradas y eventos |
| `supervisor.py` | Un job activo, preflight, recuperación, timeout, cancelación y recolección |
| `runner.py` | Subproceso con el Python portable de MPT; llamada directa a `app.services.task.start` |
| `processes.py`, `common.py` | Identidad PID/fecha de creación, árbol de procesos, locks y escritura atómica |
| `services.py` | Health checks HTTP y arranque/parada opcionales de servicios propios |
| `artifacts.py` | Inventario con SHA-256; MP4, imágenes, diagnostics y evaluación técnica |
| `worktrees.py` | Experimentos registrados en ramas/worktrees separados y checks de código |
| `agents.py` | Contratos `VideoAgent`, `EvaluatorAgent`, `EngineerAgent` |
| `dashboard.py`, `cli.py` | Control local, progreso, vídeo, diagnostics y eventos |

Flujo normal: `created → queued → preparing → running → collecting → evaluating → succeeded`.
También persiste `failed`, `cancelled` e `interrupted`. Un servicio indisponible
devuelve el job a la cola con backoff limitado. Una generación fallida no se relanza
automáticamente: `retry` crea otro UUID y conserva el intento anterior.

SQLite es la fuente de estado; el dashboard y el supervisor son procesos separados.
Se puede cerrar el dashboard sin afectar a la cola. El supervisor no utiliza LLM.
El lock de GPU serializa workers/checks de Factory del mismo usuario; el preflight
comprueba además la cola de ComfyUI. No reserva la GPU frente a otras aplicaciones:
evita lanzar generaciones simultáneas desde la WebUI.

## Interfaz elegida y aislamiento

El worker llama a `start(task_id, params, stop_at="video", allow_server_file_input=True)`.
Reutiliza la preparación de materiales locales de la CLI de MPT. No necesita
Streamlit 8501 ni controlar un navegador. Esta interfaz conserva `VideoParams` y
el pipeline actual sin añadir endpoints ni modificar el core.

Cada job usa `data/runs/<UUID>/storage`, incluido el material temporal. El wrapper
redirige las funciones de storage en su propio proceso, desactiva Redis/publicación,
bloquea el guardado de configuración y aplica un audit hook contra escrituras en
los árboles fuente. Lee los recursos y la configuración existente de MPT.
El audit hook protege frente a escrituras accidentales de Python; no es un sandbox
de seguridad frente a código experimental hostil o ejecutables externos.

No hay publicación, auto-merge, promoción automática, ni implementación LLM del
Engineer Agent. Los experimentos solo pueden apuntar a worktrees registrados.
La evaluación actual comprueba integridad técnica; **no certifica calidad visual,
identidad ni exactitud factual**. El resultado conserva `human_review_required=true`.

## Instalación en Windows

Requisitos: Git en PATH, Python 3.11 o superior para Factory, instalación portable de
MPT ya operativa, y FFmpeg/ffprobe. Factory crea su propio `.venv`; no instala sus
dependencias en el Python portable de MPT.

Extrae la carpeta del paquete en `D:\Apps\MPT-Agent-Factory`. El ZIP incluye también
un bundle Git con el historial local; es opcional para ejecutar. Si prefieres
clonarlo, usa `git clone <ruta-al-bundle> D:\Apps\MPT-Agent-Factory` cuando el destino
todavía no exista. No se ha creado un remoto de GitHub.

Desde PowerShell:

```powershell
Set-Location 'D:\Apps\MPT-Agent-Factory'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
notepad .\factory.toml
```

El script usa `py -3.11`. Si tienes otro Python compatible, puedes pasar
`-Python 'C:\ruta\python.exe'`. `factory.toml` se copia solo si no existe.
Confirma estas rutas, ya incluidas en el ejemplo:

```toml
[mpt]
root = 'D:\Apps\MoneyPrinterTurbo-Portable-Windows-1.3.6\MoneyPrinterTurbo'
python = 'D:\Apps\MoneyPrinterTurbo-Portable-Windows-1.3.6\lib\python\python.exe'
branch = 'moneyprinter_qwen21_quality_v3_1'
worktrees_dir = 'D:\Apps\MPT-worktrees'
```

El repo MPT debe estar en esa rama y sin modificaciones rastreadas. No borres sus
backups `.bak` ni hagas `git add .` para satisfacer este requisito. Si tiene cambios
propios pendientes, revísalos antes de ejecutar Factory; no se hace stash automático.
Su `config.toml` debe existir y funcionar con tu stack habitual.

En `[factory]`, `data_dir = 'data'` queda dentro de Factory, separado de MPT.
Si `ffprobe` no está en PATH, encuentra el ejecutable y escribe su ruta absoluta
en `factory.ffprobe`:

```powershell
Get-ChildItem 'D:\Apps\MoneyPrinterTurbo-Portable-Windows-1.3.6' -Recurse -Filter ffprobe.exe
.\.venv\Scripts\python.exe -m mpt_factory doctor --probe-mpt
```

Revisa el JSON de `doctor`: `mpt_import_ok` debe ser `true`, `ffprobe` debe mostrar
versión y no debe haber `git_error`. El comando genera un informe: no interpreta
todos los campos negativos como un código de salida distinto de cero.

## Primera prueba: generación local sin modelos

Esta prueba usa MPT real, una imagen de prueba generada localmente y audio sin voz.
No requiere 8080/8090/8188, TTS de red ni referencias externas.

```powershell
Set-Location 'D:\Apps\MPT-Agent-Factory'
.\.venv\Scripts\python.exe .\scripts\make-local-smoke.py
$jobId = .\.venv\Scripts\python.exe -m mpt_factory create .\data\smoke-inputs\local-smoke.json --queue | ConvertFrom-Json
.\.venv\Scripts\python.exe -m mpt_factory supervisor --once
.\.venv\Scripts\python.exe -m mpt_factory show $jobId
```

Debe finalizar en `succeeded`, registrar artifacts y tener un MP4 de duración
positiva. Esa es la expectativa de la prueba en tu PC, no un resultado ya medido
en Windows. Un job `local` no genera `precision_diagnostics.json`.

Abre el dashboard en una segunda terminal:

```powershell
Set-Location 'D:\Apps\MPT-Agent-Factory'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dashboard.ps1
```

Visita `http://127.0.0.1:8600`. Selecciona el UUID y comprueba el vídeo, el resultado
técnico y los eventos. Después puedes dejar el supervisor en ejecución continua:

```powershell
Set-Location 'D:\Apps\MPT-Agent-Factory'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-supervisor.ps1
```

No ejecutes el supervisor continuo y `supervisor --once` a la vez con la misma DB.
`--until-idle` procesa la cola hasta vaciarla; respeta también los reintentos de
preflight diferidos. El modo continuo puede esperar nuevas entradas durante horas;
no se ha realizado aún una prueba de carga de varias horas.

## Stack local y primera generación Qwen

| Servicio | Comprobación configurada | Alcance |
| --- | --- | --- |
| llama.cpp 8080 | `/health`, clave `status` | Requerido por los jobs de ejemplo de imágenes |
| bridge 8090 | `/`, respuesta 404 | Solo liveness HTTP; no demuestra que el workflow funcione |
| ComfyUI 8188 | `/system_stats`, clave `system`; `/queue` | Salud y cola libre antes de generar |
| WebUI MPT 8501 | `/_stcore/health` | Opcional, no interviene en la generación headless |
| Dashboard Factory 8600 | `/_stcore/health` | UI local independiente |

Mantén inicialmente tus launchers habituales. Los `command=[]` de llama/bridge/ComfyUI
son deliberados: el repositorio no aporta todas las rutas reales a ejecutables/modelos.
Puedes rellenarlos con arrays argv y `cwd` propios y activar `auto_start=true`.
No son cadenas de shell. Conserva los flags de ComfyUI
`--disable-async-offload --disable-pinned-memory`. Los tokens opcionales se leen de
variables de entorno (`token_env`), sin copiarlos al JSON de jobs.

```powershell
.\.venv\Scripts\python.exe -m mpt_factory services status
$qwenJobId = .\.venv\Scripts\python.exe -m mpt_factory create .\examples\first-job.json --queue | ConvertFrom-Json
.\.venv\Scripts\python.exe -m mpt_factory show $qwenJobId
```

Con el supervisor continuo arrancado, seguirá el job automáticamente. Deben recogerse
MP4, imágenes y `precision_diagnostics.json` schema 3. Revisa los tres en el dashboard.
Este ejemplo usa Edge TTS, que puede necesitar conexión a Internet según tu configuración.
Después adapta las seis rutas `D:/Refs/SX70/...` de `examples/polaroid-job.json` a tus
archivos reales; las descripciones y roles se conservan en el snapshot del job.
No cambies `identity/detail` por `continuity`: en el código auditado, continuity es
metadato del planner; los roles manuales admitidos son `identity/detail/internal/context/other`.

Para gestión explícita, después de configurar el argv real:

```powershell
.\.venv\Scripts\python.exe -m mpt_factory services start comfyui
.\.venv\Scripts\python.exe -m mpt_factory services stop comfyui
```

Factory solo detiene servicios que inició y cuya identidad PID sigue coincidiendo;
no mata procesos por puerto ni adopta servicios externos. Bloquea la parada mientras
hay jobs activos. El 404 de 8090 no sustituye la prueba real de generación.

## Recuperación y cancelación

- Ctrl+C detiene el supervisor; el worker separado sigue ejecutándose. Reinicia el
  supervisor con el mismo `factory.toml` para recuperar el intento y su resultado.
- El worker escribe `worker.json` con el PID de su intérprete real. En Windows,
  donde un launcher puede tener otro PID, el supervisor espera ese handshake y
  valida PID, argv del request, estado y fecha de creación antes de recuperar o
  cancelar.
- Se almacena PID y fecha exacta de creación. Si falta la identidad tras un crash,
  se busca el argumento exacto del `request.json` único del job. No se relanza a ciegas.
- En Linux con `/proc` de un namespace exterior se traduce el PID antes de señalar
  o esperar procesos. Windows usa psutil directamente. Un PID reutilizado no basta
  para autorizar la cancelación.
- `cancel` requiere que el supervisor esté corriendo para aplicarse a un worker
  activo. Cancela su árbol de procesos local y conserva artifacts parciales.
- Cancelar un cliente no interrumpe necesariamente un workflow ya recibido por
  ComfyUI. Factory no envía un `/interrupt` global; espera la cola libre antes del
  siguiente job de imágenes.
- Si desaparece el worker sin resultado, se registra `interrupted`. Los errores
  inesperados de control de procesos mantienen el job activo y bloquean nuevas
  generaciones hasta resolverlos; consulta `supervisor_error` en los eventos.

```powershell
.\.venv\Scripts\python.exe -m mpt_factory cancel $jobId
.\.venv\Scripts\python.exe -m mpt_factory retry $jobId
.\.venv\Scripts\python.exe -m mpt_factory list
```

`retry` solo admite estados `failed/interrupted/cancelled`. Para comprobar recuperación
y cancelación con procesos de prueba, sin lanzar modelos:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_recovery.py tests/test_services.py
```

La suite completa requiere `ffmpeg` y `ffprobe` en PATH:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pytest -q
```

## Benchmark SX-70 preparado

Con el supervisor ya iniciado y las seis imágenes en `D:\Refs\SX70`, este comando
valida la configuración, toma el pack `identity/detail` en el orden del benchmark
y encola exactamente un job. No modifica MPT ni ejecuta la generación:

```powershell
Set-Location 'D:\Apps\MPT-Agent-Factory'
.\.venv\Scripts\python.exe .\scripts\queue-polaroid.py --references 'D:\Refs\SX70'
```

El helper exige el modelo efectivo `qwen-image-2.1-precision` y los ajustes Balanced
auditados. Si alguna ruta o ajuste no coincide, termina antes de crear el job.
Consulta `examples/polaroid-job.json` y `WORK_HANDOFF.md` para el JSON y las seis
descripciones restauradas.

## Experimentos aislados

```powershell
$experiment = .\.venv\Scripts\python.exe -m mpt_factory experiment create prueba-v01 | ConvertFrom-Json
$experiment.path
.\.venv\Scripts\python.exe -m mpt_factory experiment check $experiment.id
.\.venv\Scripts\python.exe -m mpt_factory create .\examples\first-job.json --experiment $experiment.id --queue
```

`create` añade un worktree `factory/exp-...` al commit estable; copia `config.toml`
solo si Git lo ignora. Cualquier edición experimental debe hacerse en
`$experiment.path`. `check` ejecuta compileall y pytest con el Python de MPT en ese
worktree; depende de que el entorno portable tenga las dependencias de tests.
Estos checks no equivalen a un benchmark visual ni permiten promocionar código.
No hay limpieza automática de worktrees ni retención automática de artifacts en v0.1.

## Archivos operativos

- `factory.toml`: configuración local ignorada por Git.
- `data/factory.sqlite3`: jobs, estados, eventos, artifacts, experimentos y servicios.
- `data/inputs/<UUID>/`: copias de materiales/referencias al crear el job.
- `data/runs/<UUID>/`: request, progreso, resultado, provenance, evaluación,
  inventario, logs y storage MPT aislado.
- `data/logs/factory.jsonl`: exportación de eventos SQLite; tras un crash puede
  repetir un evento, identificable por `seq`.
- `data/runs/<UUID>/mpt.jsonl`: log estructurado del pipeline. `worker.log` conserva
  stdout/stderr local; revísalo antes de compartirlo.
- `data/experiments/<id>/`: logs y resultados de checks.

Los paths relativos se interpretan respecto al TOML o JSON correspondiente.
Para usar otra configuración, `--config ruta.toml` va **antes** del subcomando.
No copies un SQLite activo sin sus ficheros WAL; detén el supervisor para una copia
consistente o usa la API de backup de SQLite.

## Pendiente después de v0.1

Benchmark Precision SX-70, evaluación visual/factual y prueba prolongada de cola;
benchmarks repetidos; Engineer Agent que proponga
experimentos; ejecución como servicio del sistema y políticas de retención.
El código actual no publica ni se modifica a sí mismo.

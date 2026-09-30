"""Validate benchmark inputs only; never execute or simulate the SX-70 pipeline."""
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mpt_factory.common import atomic_json


path = Path(__file__).resolve().parents[1] / 'scripts' / 'queue-polaroid.py'
module_spec = importlib.util.spec_from_file_location('queue_polaroid', path)
benchmark = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(benchmark)


# Independent literal protects against omissions and accidental benchmark rewrites.
CONTROLLED_SCRIPT = """La Polaroid SX-70 parece revelar una fotografía casi por arte de magia, pero todo el proceso ocurre automáticamente dentro de la cámara y de la propia hoja de película.

Cuando pulsas el disparador, la luz entra a través del objetivo y queda registrada en las capas fotosensibles de la película.

Después de la exposición, el mecanismo de la cámara expulsa la fotografía hacia el exterior. Durante la salida, la hoja pasa entre los rodillos de la cámara, que ejercen presión y distribuyen el reactivo necesario para iniciar el revelado.

La reacción química sucede entre las capas de la película, por lo que gran parte de este proceso no puede observarse directamente desde fuera.

Al principio, la misma fotografía recién expulsada apenas muestra información. Poco a poco empiezan a aparecer formas, contraste y color.

Durante los siguientes minutos, esa misma imagen continúa desarrollándose. Los detalles se hacen más claros y los colores se estabilizan progresivamente.

Finalmente, la fotografía alcanza su aspecto definitivo. En una SX-70, exposición, expulsión y revelado forman parte de un único proceso automático que comienza con una sola pulsación del disparador."""


def test_polaroid_contains_exact_controlled_script():
    spec = json.loads(benchmark.BENCHMARK.read_text(encoding='utf-8'))
    assert spec['params']['video_script'] == CONTROLLED_SCRIPT
    assert spec['params']['subtitle_enabled'] is False


def test_queue_polaroid_preserves_script_in_sqlite_and_snapshot(stack, tmp_path, monkeypatch, capsys):
    cfg, db = stack
    cfg = replace(cfg, services=tuple({'name': n} for n in ('llama', 'bridge', 'comfyui')))
    monkeypatch.setattr(benchmark, 'load_config', lambda _: cfg)
    (cfg.mpt_root / 'config.toml').write_text(
        '[app]\nopenai_image_precision_model="qwen-image-2.1-precision"\n', encoding='utf-8')
    spec = json.loads(benchmark.BENCHMARK.read_text(encoding='utf-8'))
    refs = tmp_path / 'references'
    refs.mkdir()
    for item in spec['references']:
        (refs / Path(item['path']).name).write_bytes(b'input-snapshot-test-only')
    # Executes the real helper/create_job/SQLite path, never a supervisor or worker.
    assert benchmark.main(['--references', str(refs)]) == 0
    job_id = json.loads(capsys.readouterr().out)
    job = db.get(job_id)
    stored = json.loads(job['spec'])
    snapshot = json.loads((cfg.data / 'inputs' / job_id / 'job.json').read_text(encoding='utf-8'))
    assert job['state'] == 'queued'
    assert stored['params'] == spec['params'] == snapshot['params']
    assert stored['params']['video_script'] == CONTROLLED_SCRIPT
    assert [r['description'] for r in stored['references']] == [r['description'] for r in spec['references']]
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


@pytest.mark.parametrize('script', [None, '', '  ', 123])
def test_queue_polaroid_rejects_missing_or_empty_script(stack, tmp_path, monkeypatch, script):
    cfg, db = stack
    monkeypatch.setattr(benchmark, 'load_config', lambda _: cfg)
    monkeypatch.setattr(benchmark, 'verify_settings', lambda _: None)
    spec = json.loads(benchmark.BENCHMARK.read_text(encoding='utf-8'))
    if script is None:
        spec['params'].pop('video_script')
    else:
        spec['params']['video_script'] = script
    invalid = tmp_path / 'invalid-job.json'
    atomic_json(invalid, spec)
    monkeypatch.setattr(benchmark, 'BENCHMARK', invalid)
    with pytest.raises(ValueError, match='controlled video_script'):
        benchmark.main([])
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_reference_recovery_uses_historical_manifest_without_changing_order(tmp_path):
    spec = json.loads(benchmark.BENCHMARK.read_text())
    for item in spec['references']:
        item['path'] = str(tmp_path / 'missing-originals' / Path(item['path']).name)
    # Only reference lookup is tested: these inert files are never sent to MPT.
    folder = tmp_path / 'storage' / 'tasks' / benchmark.BASELINE_TASK / 'user_references'
    folder.mkdir(parents=True)
    entries = []
    for index, item in enumerate(spec['references']):
        stored = f'reference-{index}-opaque.png'
        (folder / stored).write_bytes(b'lookup-test-only')
        entries.append({'stored': stored, 'original': Path(item['path']).name})
    atomic_json(folder / 'manifest.json', {'files': list(reversed(entries))})
    found = benchmark.reference_files(tmp_path, spec)
    assert [f.name for f in found] == [e['stored'] for e in entries]
    (folder / entries[0]['stored']).unlink()
    with pytest.raises(ValueError, match='pack not found'):
        benchmark.reference_files(tmp_path, spec)


def test_reference_manifest_cannot_read_outside_its_pack(tmp_path):
    spec = {'references': [{'path': 'missing/reference.png'}]}
    folder = tmp_path / 'storage' / 'tasks' / benchmark.BASELINE_TASK / 'user_references'
    atomic_json(folder / 'manifest.json', {'files': [
        {'original': 'reference.png', 'stored': '../outside.png'}]})
    with pytest.raises(ValueError, match='escapes'):
        benchmark.reference_files(tmp_path, spec)


def test_benchmark_preflight_requires_precision_and_single_candidate(tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text('[app]\nopenai_image_model="klein"\n')
    with pytest.raises(ValueError, match='Precision model'):
        benchmark.verify_settings(tmp_path)
    config.write_text('[app]\nopenai_image_precision_model="qwen-image-2.1-precision"\n'
                      'openai_image_balanced_precision_candidates=2\n')
    with pytest.raises(ValueError, match='precision_candidates'):
        benchmark.verify_settings(tmp_path)
    config.write_text('[app]\nopenai_image_precision_model="qwen-image-2.1-precision"\n')
    before = config.read_bytes()
    benchmark.verify_settings(tmp_path)
    assert config.read_bytes() == before

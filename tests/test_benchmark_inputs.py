"""Validate benchmark inputs only; never execute or simulate the SX-70 pipeline."""
import importlib.util
import json
from pathlib import Path

import pytest

from mpt_factory.common import atomic_json


path = Path(__file__).resolve().parents[1] / 'scripts' / 'queue-polaroid.py'
module_spec = importlib.util.spec_from_file_location('queue_polaroid', path)
benchmark = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(benchmark)


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

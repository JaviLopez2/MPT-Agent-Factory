"""Submit the existing SX-70 benchmark with its local reference pack, once.

Auxiliary tooling only: no MPT config edits, service launcher or model calls.
An already-running Factory supervisor executes the queued job normally.
"""
import argparse
import json
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

from mpt_factory.common import inside, read_json
from mpt_factory.config import load_config
from mpt_factory.db import Database
from mpt_factory.jobs import create_job
from mpt_factory.worktrees import Worktrees


BASELINE_TASK = '245b7ea7-090c-4b22-a861-073aa7d0602a'
BENCHMARK = Path(__file__).resolve().parents[1] / 'examples' / 'polaroid-job.json'


def reference_files(root, spec, directory=None):
    """Use explicit originals, then the exact historical MPT task's manifest.

    Never substitute new internet images or choose a different task silently.
    """
    names = [Path(item['path']).name for item in spec['references']]
    files = [directory / name for name in names] if directory else [Path(item['path']) for item in spec['references']]
    if all(file.is_file() for file in files):
        return files
    if directory is not None:
        raise ValueError('Missing reference files: ' + ', '.join(str(f) for f in files if not f.is_file()))
    folder = root / 'storage' / 'tasks' / BASELINE_TASK / 'user_references'
    manifest = read_json(folder / 'manifest.json', {})
    originals = {}
    for item in manifest.get('files', []) if isinstance(manifest, dict) else []:
        if isinstance(item, dict) and item.get('original') in names and item.get('stored'):
            originals[item['original']] = inside(folder / item['stored'], folder)
    if all(name in originals and originals[name].is_file() for name in names):
        return [originals[name] for name in names]
    raise ValueError('SX-70 pack not found at D:/Refs/SX70 or in the historical MPT task. '
                     'Pass --references "<your sx70_v3_refs directory>" with the six original files.')


def verify_settings(root):
    app = tomllib.loads((root / 'config.toml').read_text(encoding='utf-8-sig')).get('app', {})
    expected = {
        'openai_image_balanced_scene_budget': 9,
        'openai_image_balanced_target_scene_duration': 6.0,
        'openai_image_balanced_min_scene_duration': 4.0,
        'openai_image_balanced_precision_ratio': 0.65,
        'openai_image_balanced_precision_size': '768x1376',
        'openai_image_balanced_qwen_steps': 20,
        'openai_image_balanced_precision_candidates': 1,
        'openai_image_scene_factual_audit_enabled': True,
        'openai_image_primary_identity_lock_enabled': True,
        'openai_image_continuity_edit_chain_enabled': True,
        'openai_image_precision_retry_on_true_failure_enabled': True,
        'openai_image_near_duplicate_qa_enabled': True,
    }
    for key, default in expected.items():
        if app.get(key, default) != default:
            raise ValueError(f'{key} differs from the audited Balanced benchmark setting ({default!r}). '
                             'Review your MPT configuration before submitting; no file was changed.')
    model = app.get('openai_image_precision_model') or app.get('openai_image_model')
    if model != 'qwen-image-2.1-precision':
        raise ValueError('The effective Precision model must be qwen-image-2.1-precision. '
                         'A successful Standard-only forest job does not verify this route.')
    if int(app.get('openai_image_manual_reference_max_images', 12)) < 6:
        raise ValueError('The MPT manual reference library limit must allow all six references.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='factory.toml')
    parser.add_argument('--references', type=Path, help='Directory containing the six original SX-70 images')
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    db = Database(cfg.db)
    Worktrees(cfg, db).stable()
    verify_settings(cfg.mpt_root)
    spec = json.loads(BENCHMARK.read_text(encoding='utf-8'))
    files = reference_files(cfg.mpt_root, spec, args.references)
    # Preserve original filenames in the snapshot/manifest, even when MPT's
    # historical storage uses opaque reference-XX-UUID filenames.
    with tempfile.TemporaryDirectory(prefix='sx70-submit-', dir=cfg.data) as temporary:
        for item, source in zip(spec['references'], files):
            target = Path(temporary) / Path(item['path']).name
            shutil.copy2(source, target)
            item['path'] = str(target)
        job = create_job(cfg, db, spec, Path(temporary))
    db.transition(job, 'queued')
    print(json.dumps(job))
    print('SX-70 queued once. The running Factory supervisor will execute it. '
          'If stopped, run: python -m mpt_factory supervisor', file=sys.stderr)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)

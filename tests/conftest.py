from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mpt_factory.config import Config
from mpt_factory.db import Database


@pytest.fixture
def stack(tmp_path):
    """Contract fixture, not a simulated production backend. Uses real processes/FFmpeg."""
    root = tmp_path / "mpt"
    root.mkdir()
    files = {
        ".gitignore": "config.toml\n__pycache__/\n",
        "app/__init__.py": "",
        "app/config/__init__.py": "",
        "app/config/config.py": "_cfg = {'app': {'upload_post_auto_upload': True}}\napp = dict(_cfg['app'])\nui = {}\ndef save_config(): pass\n",
        "app/models/__init__.py": "",
        "app/models/schema.py": '''
class VideoParams:
    model_fields = dict.fromkeys(['video_subject', 'video_source', 'video_script', 'subtitle_enabled'])
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
    @classmethod
    def model_validate(cls, values): return cls(**values)
    def model_dump(self, **kwargs): return dict(self.__dict__)
''',
        "app/utils/__init__.py": "",
        "app/utils/utils.py": '''
from pathlib import Path
def storage_dir(sub_dir='', create=False):
    path = Path(__file__).resolve().parents[2] / 'storage' / sub_dir
    if create: path.mkdir(parents=True, exist_ok=True)
    return str(path)
def task_dir(sub_dir=''):
    path = Path(storage_dir()) / 'tasks' / sub_dir
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
''',
        "app/services/__init__.py": "",
        "app/services/state.py": '''
class MemoryState:
    def __init__(self): self.tasks = {}
    def update_task(self, task_id, **kwargs): self.tasks[task_id] = kwargs
    def get_task(self, task_id): return self.tasks.get(task_id)
state = MemoryState()
''',
        "app/services/task.py": '''
import base64, json, subprocess, time
from pathlib import Path
from types import SimpleNamespace
from app.config import config
from app.services import state as sm
from app.utils import utils
const = SimpleNamespace(TASK_STATE_FAILED=-1)
upload_post = SimpleNamespace(cross_post_video=None)
def _schedule_cross_post(*args): raise AssertionError('Publishing must be blocked')
def start(task_id, params, stop_at, allow_server_file_input):
    assert config.app['upload_post_auto_upload'] is False
    assert config.app['enable_redis'] is False
    assert stop_at == 'video' and allow_server_file_input
    sm.state.update_task(task_id, state=4, progress=40)
    root = Path(utils.task_dir(task_id))
    if params.video_subject == 'sleep': time.sleep(60)
    if params.video_subject == 'fail': return {'state':-1,'error':'fixture failure'}
    if params.video_subject == 'write-stable':
        (Path(__file__).resolve().parents[2] / 'forbidden.txt').write_text('forbidden')
    final = root / 'final-1.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
       'color=c=blue:s=64x64:d=0.2','-c:v','libx264','-pix_fmt','yuv420p',str(final)], check=True)
    (root/'openai-image-fixture.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl9sAAAAASUVORK5CYII='))
    (root/'precision_diagnostics.json').write_text(json.dumps({'schema_version':3,'status':'completed',
        'scene_count':1,'generated_scene_count':1,'plan_scenes':[{'scene':1,'route':'standard'}]}))
    sm.state.update_task(task_id,state=1,progress=100)
    return {'videos':[str(final)]}
''',
        "cli.py": '''
import argparse
from app.models.schema import VideoParams
def parse_args(args):
    parser=argparse.ArgumentParser()
    parser.add_argument('--video-subject')
    parser.add_argument('--video-source')
    return parser.parse_known_args(args)[0]
def build_video_params(args): return VideoParams(video_subject=args.video_subject,video_source=args.video_source)
def prepare_cli_files(params,stop_at): pass
''',
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "stable", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", *files], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Factory Test", "-c", "user.email=factory-test@example.invalid", "commit", "-m", "Fixture"], check=True, capture_output=True)
    (root / "config.toml").write_text("[app]\nupload_post_auto_upload=true\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    config = Config(tmp_path / "factory.toml", data, root, Path(sys.executable), "stable",
                    tmp_path / "worktrees", 0.05, 120, 0.05, 3, "ffprobe", ())
    return config, Database(config.db)


@pytest.fixture
def spec():
    return {"params": {"video_subject": "test", "video_source": "openai_image", "subtitle_enabled": False},
            "profile": "balanced", "reference_mode": "user_first", "references": [], "required_services": []}

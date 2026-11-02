"""业务环境与独立模型环境之间的本地进程边界。"""

import json
import os
from pathlib import Path
import subprocess

from data_analysis_agent.reviews.embedding_store import EmbeddingStoreError


class LocalEmbeddingProcess:
    def __init__(self, python, cache_dir):
        self.python = str(Path(python).resolve())
        self.cache_dir = str(Path(cache_dir).resolve())

    def encode(self, texts):
        root = Path(__file__).resolve().parents[3]
        env = {k: v for k, v in os.environ.items() if k.upper() in (
            'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA',
            'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')}
        env.update(PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   HF_HUB_DISABLE_IMPLICIT_TOKEN='1', TRANSFORMERS_OFFLINE='1')
        try:
            result = subprocess.run([self.python, '-B', str(root / 'scripts/local_embedding_worker.py')],
                input=json.dumps(dict(texts=texts, cache_dir=self.cache_dir), ensure_ascii=False),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', env=env,
                timeout=600, check=False, cwd=root)
            if result.returncode:
                raise ValueError
            value = json.loads(result.stdout)
            if 'error' in value:
                raise ValueError
            return value
        except Exception:
            raise EmbeddingStoreError('local_embedding_process_failed') from None

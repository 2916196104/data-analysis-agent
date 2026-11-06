"""Synthetic OS-process fixture. Never loads weights or reads business sources."""
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from persistent_embedding_worker import serve
from data_analysis_agent.reviews.embedding_store import SPACE

class Encoder:
    space=SPACE
    def embed_texts(self,texts):
        if texts==['crash']:os._exit(7)
        if texts==['hang']:time.sleep(30)
        if texts==['badvector']:return [[0.0]*1024]
        vector=[0.0]*1024
        vector[sum(map(ord,texts[0]))%1024]=1.0
        return [vector]

def load(cache):
    assert 'LLM_API_KEY' not in os.environ and 'DATABASE_URL' not in os.environ
    with (Path(cache)/'loads.txt').open('a') as f:f.write('loaded\n')
    return Encoder()

raise SystemExit(serve(sys.stdin,sys.stdout,load))

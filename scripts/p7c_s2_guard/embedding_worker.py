"""Candidate child entry: install deny-all network before importing model code."""
from pathlib import Path
import runpy
from f02_network import install_child

def main():
    install_child()
    target=Path(__file__).resolve().parents[2]/'scripts/persistent_embedding_worker.py'
    runpy.run_path(str(target),run_name='__main__')

if __name__=='__main__':main()

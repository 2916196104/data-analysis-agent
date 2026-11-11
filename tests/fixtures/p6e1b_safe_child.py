"""Harmless F03-only child. Writes readiness then exits or waits; no DB/network."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--work',type=Path,required=True);parser.add_argument('--token',required=True)
    parser.add_argument('--label',required=True);parser.add_argument('--depth',type=int,default=0)
    parser.add_argument('--mode',choices=['normal','sleep','parent_exit','tree'],required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    allowed=root/'.cache/p6e1b-f03/native'/args.token
    if not re.fullmatch('[a-f0-9]{32}',args.token) or args.work!=allowed or args.work.resolve()!=allowed:return 2
    if not re.fullmatch('[a-z0-9_-]+',args.label) or not 0<=args.depth<=4:return 2
    args.work.mkdir(parents=True,exist_ok=True)
    ready=args.work/f'{args.label}-{args.depth}.ready.json'
    if args.mode in ('parent_exit','tree') and args.depth>0:
        mode='tree' if args.depth>1 else 'sleep'
        subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--work',str(args.work),'--token',args.token,
            '--label',args.label,'--depth',str(args.depth-1),'--mode',mode],stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
        child_ready=args.work/f'{args.label}-{args.depth-1}.ready.json'
        deadline=time.monotonic()+15
        while not child_ready.exists() and time.monotonic()<deadline:time.sleep(0.01)
        if not child_ready.exists():return 3
    with ready.open('x',encoding='utf-8') as stream:
        json.dump({'token':args.token,'pid':os.getpid(),'parent_pid':os.getppid(),'mode':args.mode},stream)
    print(f'P6SAFE READY {args.label} {args.depth} {os.getpid()}',flush=True)
    print(f'P6SAFE STDERR {args.label}',file=sys.stderr,flush=True)
    if args.mode in ('normal','parent_exit'):return 0
    threading.Event().wait(40)
    return 0


if __name__=='__main__':raise SystemExit(main())

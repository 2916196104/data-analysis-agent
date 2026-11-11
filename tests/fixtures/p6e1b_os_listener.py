"""Dedicated harmless listener: no accept/connect, credentials, DB or PostgreSQL execution."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import threading


def main():
    p=argparse.ArgumentParser();p.add_argument('--token',required=True);p.add_argument('--host',choices=('127.0.0.1','::1'),required=True)
    p.add_argument('--port',type=int,choices=(0,55441),required=True);a=p.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',a.token):return 2
    root=Path(__file__).resolve().parents[2];folder=root/'.cache/p6e1b-os-preflight/listeners'/a.token
    if not folder.is_dir() or folder.resolve()!=folder:return 2
    family=socket.AF_INET6 if ':' in a.host else socket.AF_INET
    with socket.socket(family,socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        if family==socket.AF_INET6:listener.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
        listener.bind((a.host,a.port));listener.listen(1)
        with (folder/'ready.pending.json').open('x',encoding='utf-8') as f:
            json.dump({'pid':os.getpid(),'host':a.host,'port':listener.getsockname()[1],'token':a.token,
                'so_acceptconn':listener.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)},f)
        (folder/'ready.pending.json').rename(folder/'ready.json')
        threading.Event().wait(30)
    return 0


if __name__=='__main__':raise SystemExit(main())

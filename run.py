#!/usr/bin/env python3
"""Local startup. A single process owns the SQLite task worker."""
import argparse
import os
import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Start PaperStudio locally')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-demo', action='store_true', help='Do not seed the self-authored PDF fixture')
    args = parser.parse_args()
    if args.no_demo:
        os.environ['PAPERSTUDIO_SEED_DEMO'] = '0'
    uvicorn.run('app.main:app', host=args.host, port=args.port, workers=1)

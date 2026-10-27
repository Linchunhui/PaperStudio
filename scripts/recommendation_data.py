#!/usr/bin/env python3
"""Local operator tools for the existing PaperStudio database; no model downloads."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from run import load_env_file
if (ROOT / '.env').is_file():
    load_env_file(ROOT / '.env')
configured_data = os.environ.get('PAPERSTUDIO_DATA_DIR')
if configured_data and not Path(configured_data).is_absolute():
    os.environ['PAPERSTUDIO_DATA_DIR'] = str((ROOT / configured_data).resolve())
from fastapi import HTTPException
from app import core, taxonomy
from app import recommendation_vectors as vectors
from app.recommendation_content import embedding_document
from app.recommendation_routes import StructuredInput, VectorBatch


def records(path):
    with path.open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError()
            except ValueError:
                raise ValueError(f'{path.name} 第 {number} 行必须是 JSON 对象') from None
            yield value


def export_inputs(output, *, require_abstract=False):
    count, skipped = 0, 0
    # Exclusive creation avoids silently replacing a completed model input batch.
    with output.open('x', encoding='utf-8') as stream, core.db() as con:
        for row in con.execute('SELECT data FROM catalog_papers ORDER BY id'):
            item = embedding_document(json.loads(row[0]))
            if require_abstract and item['input_type'] == 'title_only':
                skipped += 1
                continue
            record = {**item, 'entity_kind': 'paper', 'entity_id': item['catalog_paper_id'],
                      'input_hash': item['text_hash'], 'role': 'document'}
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            count += 1
        for kind, entries in (('category', taxonomy.CATEGORIES), ('topic', taxonomy.TOPICS)):
            for identifier in entries:
                stream.write(json.dumps(vectors.interest_document(kind, identifier), ensure_ascii=False) + '\n')
                count += 1
    return {'exported': count, 'papers_without_abstract_skipped': skipped}


def import_vector_file(path, spec):
    count, batch = 0, []
    for item in records(path):
        batch.append(item)
        if len(batch) == 100:
            vectors.import_vectors(VectorBatch(**{**spec, 'items': batch, 'activate': False}))
            count += len(batch)
            batch = []
    if batch:
        vectors.import_vectors(VectorBatch(**{**spec, 'items': batch, 'activate': False}))
        count += len(batch)
    return {'imported': count, 'space_id': spec['space_id'], 'activated': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export-inputs')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--require-abstract', action='store_true')
    ingest = commands.add_parser('import-vectors')
    ingest.add_argument('file', type=Path)
    ingest.add_argument('--space', type=Path, required=True, help='JSON model-space specification')
    structured = commands.add_parser('import-content')
    structured.add_argument('file', type=Path)
    activate = commands.add_parser('activate')
    activate.add_argument('space_id')
    commands.add_parser('status')
    args = parser.parse_args(argv)
    if not (core.DATA_DIR / 'paperstudio.sqlite3').is_file():
        parser.error('未找到 PaperStudio 数据库；请先启动应用，并确认 PAPERSTUDIO_DATA_DIR 与服务一致')
    vectors.init_vectors()
    try:
        if args.command == 'export-inputs':
            result = export_inputs(args.output, require_abstract=args.require_abstract)
        elif args.command == 'import-vectors':
            result = import_vector_file(args.file, json.loads(args.space.read_text(encoding='utf-8')))
        elif args.command == 'import-content':
            count = 0
            for record in records(args.file):
                identifier = record.pop('catalog_paper_id')
                vectors.store_structured(identifier, StructuredInput(**record))
                count += 1
            result = {'imported': count}
        elif args.command == 'activate':
            result = vectors.activate_space(args.space_id)
        else:
            result = vectors.status()
        print(json.dumps(result, ensure_ascii=False))
    except (HTTPException, ValueError, OSError, KeyError) as error:
        # Already completed batches remain committed; rerunning their IDs is safe.
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        print(f'操作未完成：{detail}。已成功的批次保留，可修正后重跑。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

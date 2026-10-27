"""Paged index cards from observed metadata; no invented author biographies."""
from . import catalog, core, taxonomy


def browse(kind, q, direction, topic, institution, author, conference, page, page_size, **paper_filters):
    condition, args = catalog.filter_sql(direction=direction, topic=topic, institution=institution, author=author, conference=conference, **paper_filters)
    if kind in {'institution', 'conference'}:
        table = 'catalog_institutions' if kind == 'institution' else 'catalog_conferences'
        join, value, extra = f' JOIN {table} e ON e.paper_id=p.id', 'e.name', ''
    else:
        join, value, extra = ' JOIN catalog_facets e ON e.paper_id=p.id', 'e.value', "e.kind=?"
        args.append(kind)
    clauses = [condition[7:]] if condition else []
    if extra:
        clauses.append(extra)
    # Taxonomy search uses display names, while stored stable IDs remain filters.
    labels = {c['id']: c for c in taxonomy.snapshot()['categories']}
    labels.update({t['id']: t for c in taxonomy.snapshot()['categories'] for t in c.get('topics', [])})
    if q.strip():
        if kind in {'category', 'topic'}:
            matches = [key for key, item in labels.items() if q.casefold() in (item['name'] + ' ' + item.get('description', '')).casefold()]
            clauses.append(value + ' IN (' + ','.join('?' for _ in matches) + ')')
            args.extend(matches)
        else:
            clauses.append('instr(lower(' + value + '),?)>0')
            args.append(q.strip().casefold())
    sql = 'SELECT ' + value + ' value,count(DISTINCT p.id) count FROM catalog_papers p' + join
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' GROUP BY ' + value
    with core.db() as con:
        total = con.execute('SELECT count(*) FROM (' + sql + ')', args).fetchone()[0]
        rows = con.execute(sql + ' ORDER BY count DESC,value COLLATE NOCASE LIMIT ? OFFSET ?', [*args, page_size, (page - 1) * page_size]).fetchall()
    return {'items': [{'kind': kind, 'value': r['value'], 'name': labels.get(r['value'], {}).get('name', r['value']), 'description': labels.get(r['value'], {}).get('description', ''), 'count': r['count']} for r in rows], 'total': total, 'page': page, 'pages': (total + page_size - 1) // page_size}

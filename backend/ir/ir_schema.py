def normalize(parsed_files):
    """Normalize parser outputs into a simple IR used by downstream components.

    Output: list of dicts with keys: path, language, entities (list).
    Each entity is one of: thread, lock, variable, with optional metadata.
    """
    ir = []
    for p in parsed_files:
        base = {'path': p.get('path'), 'language': p.get('language'), 'entities': []}
        lang = p.get('language')
        if lang == 'python':
            for t in p.get('threads', []):
                base['entities'].append({'type': 'thread', 'subtype': t.get('type'), 'lineno': t.get('lineno')})
            for l in p.get('locks', []):
                base['entities'].append({'type': 'lock', 'subtype': l.get('type'), 'lineno': l.get('lineno')})
            for v in p.get('shared_variables', []):
                base['entities'].append({'type': 'variable', 'name': v})
        elif lang == 'c':
            for t in p.get('threads', []):
                base['entities'].append({'type': 'thread', 'subtype': t.get('type'), 'lineno': t.get('lineno')})
            for l in p.get('locks', []):
                base['entities'].append({'type': 'lock', 'subtype': l.get('type'), 'lineno': l.get('lineno')})
            for v in p.get('shared_variables', []):
                base['entities'].append({'type': 'variable', 'name': v})
        else:
            # fallback: include raw keys
            for k, v in p.items():
                if k not in ('path', 'language'):
                    base['entities'].append({'type': 'raw', 'key': k, 'value': v})
        ir.append(base)
    return ir

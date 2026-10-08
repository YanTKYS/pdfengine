"""Read-only B2 corpus probe; never writes PDF or trusts structural ownership."""
from collections import Counter
import json
from pathlib import Path
import platform

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content, paragraph_structure


def probe(source, page):
    observed = observe_marked_content(source, page)
    content = ContentPage(source, page)
    try:
        owners, refusals, supported = {}, Counter(), []
        for scope in observed['scopes']:
            ref = scope['association'].get('owner_reference')
            if ref:
                owners.setdefault(ref['xref'], []).append(scope)
        for scopes in owners.values():
            selected = {i for event in content.events for char in event.chars for i in char.source_orders
                        if any(s['content_byte_range'][0] <= event.operator.start < s['content_byte_range'][1]
                               and not event.invocation for s in scopes)}
            if not selected:
                continue
            try:
                bundle = paragraph_structure(content, selected)
                if bundle:
                    supported.append(dict(mcids=bundle['mcids'], owner=scopes[0]['association']['owner_reference'],
                                          glyphs=len(selected)))
            except PdfError as exc:
                refusals[str(exc)] += 1
        return dict(file=source.name, page=page, source_sha256=observed['source_sha256'],
            complete=observed['complete'], scopes=len(observed['scopes']),
            statuses=dict(Counter(s['association']['status'] for s in observed['scopes'])),
            tags=dict(Counter(s['tag'] for s in observed['scopes'])),
            nested_scopes=sum(bool(s['enclosing_scope_ids']) for s in observed['scopes']),
            supported_owner_bundles=supported, refusal_counts=dict(refusals),
            example_scopes=observed['scopes'][:3])
    finally:
        content.close()


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    rows = [probe(root / 'evaluations/realpdf/corpus' / (name + '.pdf'), page) for name, page in
            [('word_takeo_notice', 2), ('word_osaka_guideline', 1), ('word_osaka_fire_notice', 1), ('print_ubiquiti', 1)]]
    result = dict(platform=platform.platform(), python=platform.python_version(),
                  mode='read-only structure validation; no real Word edit lifecycle', results=rows)
    (Path(__file__).parent / 'structure-probe.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    for row in rows:
        print(row['file'], row['scopes'], 'scopes;', len(row['supported_owner_bundles']), 'supported leaf /P bundles')

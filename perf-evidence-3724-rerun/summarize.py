"""Validate complete balanced pairs and write per-page descriptive statistics."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median

METRICS = ['ready_ms', 'long_task_ms', 'api_serial_ms']
ap = argparse.ArgumentParser()
ap.add_argument('results', type=Path)
ap.add_argument('--out', type=Path, required=True)
args = ap.parse_args()
data = json.loads(args.results.read_text())
samples = data['samples']
by_key = {}
for sample in samples:
    key = (sample['mode'], sample['page'], sample['pair_index'], sample['variant'])
    if key in by_key:
        raise SystemExit(f'duplicate sample: {key}')
    by_key[key] = sample
pages = list(dict.fromkeys(s['page'] for s in samples))
expected_count = len(pages) * (data['metadata']['cold_pairs'] + data['metadata']['walk_pairs']) * 2
if len(pages) != 19 or len(samples) != expected_count or not data['metadata'].get('finished_at'):
    raise SystemExit(f'incomplete run: {len(pages)} pages, {len(samples)}/{expected_count} samples')
output = {'metadata': data['metadata'], 'sample_count': len(samples), 'modes': {},
          'measurement_errors': [], 'page_errors': [], 'http_failures': {},
          'notes': ['All samples kept, including the first pair; no outlier exclusions.',
                    'Descriptive medians only; eight cold pairs/four walk pairs do not establish a stable benefit.',
                    'API later-burst totals and main-thread totals overlap; neither proves a critical-path bottleneck.',
                    'Long tasks use the original accumulated-window metric, sampled just after content ready.',
                    'The old lost run is not pooled with this rerun.',
                    'Project-docs-charter retains the original tabs-ready criterion; both first-pair screenshots still show the document-body skeleton. It is not document-body readiness.',
                    'Screenshots use English UI and lack CJK fonts; Chinese demo text renders as missing-glyph boxes in both variants.']}
for mode, pair_count in [('cold', data['metadata']['cold_pairs']), ('walk', data['metadata']['walk_pairs'])]:
    rows = []
    for page in pages:
        variants = {v: [by_key[(mode, page, i, v)] for i in range(pair_count)] for v in ['base', 'head']}
        for variant, group in variants.items():
            positions = Counter(s['position'] for s in group)
            if positions != Counter({0: pair_count // 2, 1: pair_count // 2}):
                raise SystemExit(f'unbalanced order: {mode} {page} {variant}: {positions}')
        for i in range(pair_count):
            if variants['base'][i]['position'] == variants['head'][i]['position']:
                raise SystemExit(f'not interleaved: {mode} {page} {i}')
        row = {'page': page, 'pairs': pair_count, 'metrics': {}}
        ok_pairs = [i for i in range(pair_count) if all(not variants[v][i].get('error') for v in variants)]
        row['successful_pairs'] = len(ok_pairs)
        for metric in METRICS:
            base = [variants['base'][i][metric] for i in ok_pairs]
            head = [variants['head'][i][metric] for i in ok_pairs]
            if not base:
                row['metrics'][metric] = None
                continue
            differences = [h - b for b, h in zip(base, head)]
            row['metrics'][metric] = {'base_median': round(median(base), 1),
                                      'head_median': round(median(head), 1),
                                      'difference_of_medians': round(median(head) - median(base), 1),
                                      'median_paired_delta': round(median(differences), 1),
                                      'paired_delta_range': [round(min(differences), 1), round(max(differences), 1)],
                                      'head_faster_pairs': sum(d < 0 for d in differences),
                                      'base_range': [min(base), max(base)], 'head_range': [min(head), max(head)]}
        rows.append(row)
    aggregates = {}
    for metric in METRICS:
        values = [r['metrics'][metric] for r in rows if r['metrics'][metric]]
        aggregates[metric] = {'median_of_base_page_medians': round(median(v['base_median'] for v in values), 1),
                              'median_of_head_page_medians': round(median(v['head_median'] for v in values), 1),
                              'median_of_page_median_differences': round(median(v['difference_of_medians'] for v in values), 1),
                              'median_of_page_paired_deltas': round(median(v['median_paired_delta'] for v in values), 1),
                              'head_lower_page_medians': sum(v['difference_of_medians'] < 0 for v in values)}
    output['modes'][mode] = {'pages': rows, 'aggregates': aggregates}
for s in samples:
    label = {k: s[k] for k in ['mode', 'page', 'variant', 'pair_index']}
    if s.get('error'):
        output['measurement_errors'].append({**label, 'error': s['error']})
    output['page_errors'].extend({**label, **e} for e in s['page_errors'])
failures = Counter((f['phase'], f['status'], f['url'].split('/', 3)[-1]) for s in samples for f in s['http_failures'])
output['http_failures'] = [{'phase': phase, 'status': status, 'path': path, 'count': count}
                           for (phase, status, path), count in sorted(failures.items())]
args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
lines = ['# PR #3724 cold-open rerun', '', 'All times are milliseconds. Cells are base / head medians.', '']
for mode, section in output['modes'].items():
    lines += [f'## {mode}', '', '| Page | ready_ms | long_task_ms | api_serial_ms |', '|---|---:|---:|---:|']
    for row in section['pages']:
        cells = [f'{row["metrics"][metric]["base_median"]} / {row["metrics"][metric]["head_median"]}'
                 if row['metrics'][metric] else 'error' for metric in METRICS]
        lines.append('| ' + ' | '.join([row['page']] + cells) + ' |')
    lines += ['', 'Aggregate definitions and paired deltas are in summary.json.', '']
lines += ['## Limits', ''] + ['- ' + note for note in output['notes']]
lines += ['', f'Measurement errors: {len(output["measurement_errors"])}; page errors: {len(output["page_errors"])}.',
          'HTTP failures, including warm-up, are listed separately in summary.json.', '']
args.out.with_suffix('.md').write_text('\n'.join(lines))
print(json.dumps({m: s['aggregates'] for m, s in output['modes'].items()}, indent=2))

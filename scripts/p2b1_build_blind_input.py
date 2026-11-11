"""Build two historical-only blind inputs, without reading the adjudicated reference."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from p2b1_blind_protocol import CONDITIONS, NON_CLAIMS, OPINION, QUESTIONS, SCOPE, VERSION, validate

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'candidate_file_sha256': ('outputs/agent-stage2-real-business-readiness-20261111/candidate-evaluation.v1.json', 'b064b4518ba4ab80d2cfa79790608b8cf1f5a108d3eddc888a6043f10f8872b5'),
    'csv_sha256': ('data/private/.uploads/846743acd4524af3bfc9ccd80bac48a2.csv', '90d5061b67a724ee7584ea01e78d6115865294470f7a7ae29597c3a7a94acccf'),
    'row_observation_sha256': ('outputs/rag-retrieval-quality/step4b-phase2c0-20261104/attempt2-current-source-observation.json', '373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295'),
    'opinion_observation_sha256': ('outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104/prepared-v1/observed-sources.json', 'c6331fffd3c1ead27e763f84e5c3359ae62d67fd06215ce21acbb3df5e47c1fa'),
}
OUTPUT = ROOT / 'outputs/agent-stage2-real-business-readiness-20261111/p2-b1-blind-input-v1'


def load_sources(root=ROOT):
    raw = {}
    for role, (name, expected) in FILES.items():
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('source_path_invalid')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('source_digest_mismatch')
        raw[role] = data
    return raw


def build(raw):
    for role, (_, expected) in FILES.items():
        if hashlib.sha256(raw[role]).hexdigest() != expected:
            raise ValueError('source_digest_mismatch')
    dataset = json.loads(raw['candidate_file_sha256'])
    observed = json.loads(raw['opinion_observation_sha256'])
    tables = json.loads(raw['row_observation_sha256'])['source_tables']
    import io
    reader = csv.DictReader(io.StringIO(raw['csv_sha256'].decode('utf-8-sig'), newline=''))
    rows = {}; previous = reader.line_num
    for row in reader:
        rows[previous + 1] = row
        previous = reader.line_num
    reviews = {x['id']: x for x in tables['reviews']}
    annotations = {x['id']: x for x in tables['review_annotations']}
    opinions = {x['key']: x for x in observed['records']}
    units = {x['evidence_id']: x for x in dataset['evidence_units']}
    cases = {x['case_id']: x for x in dataset['cases']}
    manifest = {role: expected for role, (_, expected) in FILES.items()}
    manifest['historical_source_status'] = 'history_unproven'
    packages = []
    for case_id, (question, ids) in QUESTIONS.items():
        case = cases[case_id]
        if case['questions'] != [question] or case['candidate_evidence_ids'] != list(ids) or case['scope'] != SCOPE:
            raise ValueError('historical_case_mismatch')
        docs = []
        for evidence_id in ids:
            u = units[evidence_id]; review = reviews[u['review_id']]; annotation = annotations[u['annotation_id']]
            csv_row = rows[review['line_number']]
            if (review['review_text'] != u['review_text'] or csv_row['评论内容'] != u['review_text'] or
                    csv_row['商品ID'] != u['product_id'] or review['product_id'] != u['product_id'] or
                    annotation['review_id'] != u['review_id'] or annotation['content_sha256'] != u['review_content_sha256'] or
                    review['review_content_sha256'] != u['review_content_sha256'] or
                    annotation['computed_label_profile_key'] != SCOPE['label_profile_key'] or annotation['status'] != 'success' or
                    annotation['requested_model'] != u['annotation_requested_model'] or
                    annotation['prompt_version'] != u['annotation_prompt_version'] or
                    review['source'] != u['source'] or review['dataset_version'] != u['dataset_version'] or
                    review['published_at'] != u['published_at'] or review['line_number'] != u['line_number']):
                raise ValueError('historical_source_mismatch')
            spans = []
            for op in u['historical_opinions']:
                original = opinions[op['key']]
                if original['review_id'] != u['review_id'] or original['annotation_id'] != u['annotation_id']:
                    raise ValueError('historical_opinion_identity_mismatch')
                if any(op[k] != original[k] for k in OPINION):
                    raise ValueError('historical_opinion_mismatch')
                spans.append({k: op[k] for k in OPINION})
            docs.append({k: u[k] for k in ('evidence_id', 'review_id', 'annotation_id', 'source', 'dataset_version',
                'product_id', 'published_at', 'label_profile_key', 'annotation_requested_model', 'annotation_prompt_version',
                'review_text', 'review_text_utf8_sha256', 'review_content_sha256', 'file_sha256', 'line_number')} |
                {'original_opinion_spans': spans})
        package = {'version': VERSION, 'case_id': case_id, 'original_question': question,
                   'component_subquery': None, 'test_layer': 'historical_input_preflight_only', 'scope': SCOPE,
                   'query_conditions': CONDITIONS[case_id], 'source_manifest': manifest, 'candidate_documents': docs,
                   'evaluation_constraints': ['no_inferred_labels', 'no_unapproved_citations'],
                   'non_claimed_capabilities': NON_CLAIMS}
        validate(package)
        packages.append(package)
    return packages


def main():
    if len(sys.argv) != 1:
        raise ValueError('arguments_forbidden')
    packages = build(load_sources())
    checks = []
    for package in packages:
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/p2b1_blind_protocol.py')],
                                input=json.dumps(package, ensure_ascii=False).encode('utf8'),
                                capture_output=True, timeout=10, check=True)
        checks.append(json.loads(result.stdout))
    OUTPUT.mkdir(parents=False, exist_ok=False)
    files = {}
    for package in packages:
        path = OUTPUT / (package['case_id'] + '.blind.v1.json')
        data = (json.dumps(package, ensure_ascii=False, indent=2) + '\n').encode('utf8')
        with path.open('xb') as f:
            f.write(data)
        files[path.name] = hashlib.sha256(data).hexdigest()
    preflight = {'version': 'p2b1-preflight-v1', 'checks': checks, 'prediction_calls': 0,
                 'current_source_certification': False, 'component_execution_authorized': False}
    data = (json.dumps(preflight, ensure_ascii=False, indent=2) + '\n').encode('utf8')
    with (OUTPUT / 'preflight.json').open('xb') as f:
        f.write(data)
    files['preflight.json'] = hashlib.sha256(data).hexdigest()
    with (OUTPUT / 'artifact-sha256.json').open('x', encoding='utf8') as f:
        json.dump({'version': 'p2b1-artifacts-v1', 'files': files}, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(preflight, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Closed, stdin-only preflight contract; deliberately contains no prediction runner."""
import hashlib
import json
import sys

VERSION = 'p2b1-blind-input-v1'
QUESTIONS = {
    'RB03': ('保存的评论里，有没有明确说送货慢？请用原文支持。', ('EV08', 'EV15')),
    'RB09': ('只有制冷效果差的候选，能据此回答制冷速度慢吗？', ('EV02',)),
}
SCOPE = {'source': 'reviews_csv', 'dataset_version': 'reviews-csv', 'product_ids': ['100013588055'],
         'date_from': '2021-09-11', 'date_to': '2022-08-26',
         'stored_time_interval_utc': '[2021-09-11T00:00:00Z,2022-08-27T00:00:00Z)',
         'label_profile_key': '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'}
CONDITIONS = {
    'RB03': ['buyer_report', 'delivery_subject', 'original_quotation', 'fixed_historical_candidates',
             'separate_delivery_and_installation', 'no_population_inference'],
    'RB09': ['buyer_report', 'cooling_speed', 'fixed_historical_candidates',
             'separate_effect_airflow_and_speed', 'distinguish_unknown_insufficient_and_opposite'],
}
TOP = {'version', 'case_id', 'original_question', 'component_subquery', 'test_layer', 'scope',
       'query_conditions', 'source_manifest', 'candidate_documents', 'evaluation_constraints',
       'non_claimed_capabilities'}
DOC = {'evidence_id', 'review_id', 'annotation_id', 'source', 'dataset_version', 'product_id',
       'published_at', 'label_profile_key', 'annotation_requested_model', 'annotation_prompt_version',
       'review_text', 'review_text_utf8_sha256', 'review_content_sha256', 'file_sha256', 'line_number',
       'original_opinion_spans'}
OPINION = {'key', 'model_opinion_id', 'opinion_source', 'aspect', 'sentiment', 'quote', 'quote_start',
           'quote_end', 'embedding_input_sha256', 'effective_snapshot_sha256', 'text_builder_version',
           'label_profile_key', 'human_opinion_id', 'human_revision_id'}
SOURCE = {'candidate_file_sha256', 'csv_sha256', 'row_observation_sha256', 'opinion_observation_sha256',
          'historical_source_status'}
SOURCE_HASHES = {
    'candidate_file_sha256': 'b064b4518ba4ab80d2cfa79790608b8cf1f5a108d3eddc888a6043f10f8872b5',
    'csv_sha256': '90d5061b67a724ee7584ea01e78d6115865294470f7a7ae29597c3a7a94acccf',
    'row_observation_sha256': '373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295',
    'opinion_observation_sha256': 'c6331fffd3c1ead27e763f84e5c3359ae62d67fd06215ce21acbb3df5e47c1fa',
}
DOCUMENT_HASHES = {
    'EV08': 'baa61b944ccbbdbc6cdc57bbd776a3e16f76b5bd456fe4be24829e0f5e855bc0',
    'EV15': 'cedcdf5604888eeb1f6313f2028ed4c83a51a880a188932f467907bdd600c517',
    'EV02': '6e5e79e15251df3baade0b8673820b0fadedd1d3c581b75684ef57e3dfa4c685',
}
NON_CLAIMS = ['original_question_routing', 'production_validator', 'current_source_certification',
              'retrieval_recall', 'end_to_end_rag', 'prediction_or_score']
FORBIDDEN_READS = ['gold-reference-v1', 'human-evidence-adjudication', 'candidate_decisions',
                   'human-evidence-decisions', 'evidence-approval-increments', 'rb10-reading-increments',
                   'candidate-evidence-recheck', 'scoring', 'prediction']


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('field_whitelist_violation')


def validate(package):
    _keys(package, TOP)
    case = package.get('case_id')
    if case not in QUESTIONS or package['version'] != VERSION:
        raise ValueError('unknown_case_or_version')
    question, ids = QUESTIONS[case]
    if package['original_question'] != question or package['scope'] != SCOPE:
        raise ValueError('question_or_scope_changed')
    if package['component_subquery'] is not None or package['test_layer'] != 'historical_input_preflight_only':
        raise ValueError('unapproved_subquery_or_layer')
    if package['query_conditions'] != CONDITIONS[case] or package['evaluation_constraints'] != ['no_inferred_labels', 'no_unapproved_citations']:
        raise ValueError('conditions_changed')
    if package['non_claimed_capabilities'] != NON_CLAIMS:
        raise ValueError('coverage_changed')
    _keys(package['source_manifest'], SOURCE)
    manifest = package['source_manifest']
    if manifest['historical_source_status'] != 'history_unproven':
        raise ValueError('source_status_changed')
    if any(manifest[k] != expected for k, expected in SOURCE_HASHES.items()):
        raise ValueError('source_digest_mismatch')
    docs = package['candidate_documents']
    if not isinstance(docs, list) or [x.get('evidence_id') for x in docs if isinstance(x, dict)] != list(ids):
        raise ValueError('candidate_order_or_identity_changed')
    for doc in docs:
        _keys(doc, DOC)
        if (doc['source'], doc['dataset_version'], doc['product_id'], doc['label_profile_key']) != (
                SCOPE['source'], SCOPE['dataset_version'], SCOPE['product_ids'][0], SCOPE['label_profile_key']):
            raise ValueError('candidate_scope_mismatch')
        if not SCOPE['date_from'] <= doc['published_at'][:10] <= SCOPE['date_to']:
            raise ValueError('candidate_date_mismatch')
        if hashlib.sha256(doc['review_text'].encode('utf-8')).hexdigest() != doc['review_text_utf8_sha256']:
            raise ValueError('review_hash_mismatch')
        if doc['file_sha256'] != manifest['csv_sha256'] or not doc['original_opinion_spans']:
            raise ValueError('source_or_opinions_missing')
        for op in doc['original_opinion_spans']:
            _keys(op, OPINION)
            a, z = op['quote_start'], op['quote_end']
            if type(a) is not int or type(z) is not int or not 0 <= a < z <= len(doc['review_text']):
                raise ValueError('bad_span')
            if doc['review_text'][a:z] != op['quote'] or op['label_profile_key'] != SCOPE['label_profile_key']:
                raise ValueError('opinion_mismatch')
            if hashlib.sha256(op['quote'].encode('utf-8')).hexdigest() != op['embedding_input_sha256']:
                raise ValueError('opinion_hash_mismatch')
        canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if hashlib.sha256(canonical).hexdigest() != DOCUMENT_HASHES[doc['evidence_id']]:
            raise ValueError('frozen_document_mismatch')
    return {'status': 'INPUT_PRECHECK_ONLY', 'case_id': case, 'prediction_executed': False,
            'original_question_comparable': False, 'component_subquery_ready': False}


def main():
    # No file, plugin, runner, command, URL, or import is accepted through this boundary.
    if len(sys.argv) != 1:
        raise ValueError('arguments_forbidden')
    raw = sys.stdin.buffer.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('input_too_large')
    result = validate(json.loads(raw))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        print('{"status":"REJECTED"}', file=sys.stderr)
        sys.exit(2)

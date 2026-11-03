"""Step 4A 离线评测契约与人工指标；不被业务路径导入。"""

from collections import defaultdict
import math

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_store import SPACE_ID
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS
from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION
from data_analysis_agent.reviews.scope import model_identity

EVALUATION_VERSION = 'rag-retrieval-quality-v1'
LABEL_VERSION = 'human-relevance-v1'
APPROVED_SCOPE = dict(source='reviews_csv', dataset_version='reviews-csv',
    product_ids=['100013588055'], date_from='2021-09-11', date_to='2022-08-26',
    model='Pro/deepseek-ai/DeepSeek-V3.2',
    prompt_version='review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e')
PROFILE_KEY = '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
RULES = dict(version=LABEL_VERSION, relevant_grade=2, partial_grade=1,
    grades={'2': '直接回答核心问题，事实、主题及情感方向一致',
            '1': '主题相近但不能直接回答，或缺少必要信息',
            '0': '主题错误、情感冲突或证据不支持'},
    precision_denominator='k; missing ranks count as zero only for human-confirmed answerable queries',
    unjudged='N/A; each evaluated prefix must be fully judged',
    no_known_relevant='separate returned-candidate rate; quality metrics N/A',
    recall='N/A; complete indexed candidate judgments are not collected',
    abstention='no distance threshold; returning a candidate does not assert relevance')


class EvaluationError(ValueError):
    """仅用固定错误码，不包含人工备注、原文或配置。"""


def require(condition, code):
    if not condition:
        raise EvaluationError(code)


def validate_options(options):
    require(set(options) == set(APPROVED_SCOPE) | {'base_url'}, 'unauthorized_scope')
    require(all(options[k] == v for k, v in APPROVED_SCOPE.items()), 'unauthorized_scope')
    identity = model_identity(**{k: options[k] for k in ('model', 'base_url', 'prompt_version')})
    require(content_sha256(list(identity)) == PROFILE_KEY, 'unauthorized_label_identity')


def validate_manifest(manifest):
    require(manifest.get('version') == EVALUATION_VERSION, 'query_version_mismatch')
    require(manifest.get('scope') == APPROVED_SCOPE and manifest.get('label_profile_key') == PROFILE_KEY,
            'unauthorized_manifest_scope')
    require(manifest.get('embedding_space_id') == SPACE_ID
            and manifest.get('text_builder_version') == TEXT_BUILDER_VERSION, 'space_or_builder_mismatch')
    require(manifest.get('rules') == RULES, 'rules_mismatch')
    queries = manifest.get('queries', [])
    require(isinstance(queries, list) and 20 <= len(queries) <= 30, 'invalid_query_count')
    require(len({q['id'] for q in queries}) == len(queries), 'duplicate_query_id')
    for q in queries:
        require(isinstance(q.get('text'), str) and bool(q['text'].strip()), 'invalid_query')
        require(q.get('aspect') in ASPECTS and q.get('sentiment') in (*SENTIMENTS, 'mixed'), 'invalid_query_tags')
        require(set(q.get('filters', {})) <= {'aspect', 'sentiment'}, 'invalid_filters')
        for k, v in q['filters'].items():
            require(v in (ASPECTS if k == 'aspect' else SENTIMENTS), 'invalid_filters')
    return content_sha256(manifest)


def run_binding(run):
    """哈希绑定排名、来源和原文版本；不把文本带到公开指标中。"""
    return content_sha256({k: v for k, v in run.items() if k != 'run_id'})


def label_template(run):
    return dict(label_version=LABEL_VERSION, manifest_sha256=run['manifest_sha256'],
        run_id=run['run_id'], scope_sha256=run['scope_sha256'], human_confirmed=False, reviewer='',
        queries=[dict(id=q['id'], evidence_status='unreviewed', note='') for q in run['manifest']['queries']],
        judgments=[dict(query_id=s['query_id'], mode=s['mode'], rank=h['rank'],
            key=h['key'], grade=None, note='') for s in run['searches'] for h in s['hits']])


def validate_run(run):
    manifest = run['manifest']
    digest = validate_manifest(manifest)
    require(run.get('manifest_sha256') == digest, 'query_hash_mismatch')
    require(run.get('scope_sha256') == content_sha256([APPROVED_SCOPE, PROFILE_KEY, SPACE_ID]), 'scope_mismatch')
    require(run.get('run_id') == run_binding(run), 'run_hash_mismatch')
    expected = {(q['id'], mode) for q in manifest['queries'] for mode in ('unfiltered', 'filtered')}
    require(len(run['searches']) == len(expected)
            and {(s['query_id'], s['mode']) for s in run['searches']} == expected, 'incomplete_searches')
    queries = {q['id']: q for q in manifest['queries']}
    for search in run['searches']:
        hits = search['hits']
        filters = queries[search['query_id']]['filters'] if search['mode'] == 'filtered' else {}
        require(search['filters'] == filters and len(hits) <= 5, 'invalid_search_filters_or_top_k')
        require([h['rank'] for h in hits] == list(range(1, len(hits) + 1)), 'invalid_ranks')
        require(all(type(h['rank']) is int for h in hits), 'invalid_ranks')
        require(len({h['key'] for h in hits}) == len(hits), 'duplicate_hit')
        for h in hits:
            require(h.get('evidence_valid') is True, 'invalid_or_stale_evidence')
            require(h['aspect'] in ASPECTS and h['sentiment'] in SENTIMENTS, 'invalid_hit_tags')
            require(all(h[k] == v for k, v in filters.items()), 'hit_filter_mismatch')
            require(type(h['cosine_distance']) in (int, float)
                    and math.isfinite(h['cosine_distance'])
                    and -1e-5 <= h['cosine_distance'] <= 2.00001, 'invalid_distance')
        require([h['cosine_distance'] for h in hits] == sorted(h['cosine_distance'] for h in hits),
                'invalid_distance_order')


def score_run(run, labels):
    validate_run(run)
    for k, expected in (('label_version', LABEL_VERSION), ('manifest_sha256', run['manifest_sha256']),
                        ('run_id', run['run_id']), ('scope_sha256', run['scope_sha256'])):
        require(labels.get(k) == expected, 'label_binding_mismatch')
    require(type(labels.get('human_confirmed')) is bool, 'invalid_confirmation')
    require(isinstance(labels.get('reviewer'), str), 'invalid_reviewer')
    confirmed = labels['human_confirmed']
    require(not confirmed or bool(labels['reviewer'].strip()), 'human_reviewer_required')
    queries = {q['id']: q for q in run['manifest']['queries']}
    statuses = labels.get('queries', [])
    require(len(statuses) == len(queries) and {q['id'] for q in statuses} == set(queries), 'query_labels_mismatch')
    status = {q['id']: q['evidence_status'] for q in statuses}
    require(all(v in ('unreviewed', 'answerable', 'no_known_relevant', 'insufficient_sample')
                for v in status.values()), 'invalid_evidence_status')
    expected = {(s['query_id'], s['mode'], h['rank'], h['key']) for s in run['searches'] for h in s['hits']}
    judgments = labels.get('judgments', [])
    keys = [(j['query_id'], j['mode'], j['rank'], j['key']) for j in judgments]
    require(len(keys) == len(expected) and set(keys) == expected, 'judgments_mismatch')
    grades = {}
    for key, j in zip(keys, judgments):
        grade = j['grade']
        require(grade is None or type(grade) is int and grade in (0, 1, 2), 'invalid_human_grade')
        require(status[key[0]] != 'no_known_relevant' or grade != 2, 'contradictory_evidence_status')
        grades[key] = grade
    rows = []
    for search in run['searches']:
        qid, mode, hits = search['query_id'], search['mode'], search['hits']
        values = [grades[qid, mode, h['rank'], h['key']] for h in hits]
        row = dict(query_id=qid, mode=mode, aspect=queries[qid]['aspect'], sentiment=queries[qid]['sentiment'],
                   evidence_status=status[qid], returned=len(hits), judged=sum(v is not None for v in values),
                   hit_details=[dict(rank=h['rank'], key=h['key'], aspect=h['aspect'], sentiment=h['sentiment'],
                                     grade=g) for h, g in zip(hits, values)],
                   recall_at_5=None, recall_reason='complete_200_candidate_judgments_not_available')
        for k in (1, 3, 5):
            prefix = values[:k]
            ready = confirmed and status[qid] == 'answerable' and all(v is not None for v in prefix)
            row[f'precision_at_{k}'] = sum(v == 2 for v in prefix) / k if ready else None
            row[f'partial_at_{k}'] = sum(v == 1 for v in prefix) / k if ready else None
            row[f'denominator_at_{k}'] = k if ready else None
        ready = row['precision_at_5'] is not None
        relevant_ranks = [i + 1 for i, v in enumerate(values) if v == 2]
        row['hit_rate_at_5'] = int(bool(relevant_ranks)) if ready else None
        row['mrr_at_5'] = (1 / relevant_ranks[0] if relevant_ranks else 0) if ready else None
        no_evidence = confirmed and status[qid] == 'no_known_relevant' and all(v is not None for v in values)
        row['no_known_candidate_return_at_5'] = int(bool(hits)) if no_evidence else None
        row['no_known_irrelevant_count_at_5'] = sum(v == 0 for v in values) if no_evidence else None
        row['no_known_partial_count_at_5'] = sum(v == 1 for v in values) if no_evidence else None
        rows.append(row)
    metric_names = [f'{prefix}_at_{k}' for prefix in ('precision', 'partial') for k in (1, 3, 5)] + [
        'hit_rate_at_5', 'mrr_at_5', 'no_known_candidate_return_at_5']

    def aggregate(selected):
        return {name: dict(value=sum(v) / len(v) if v else None, query_denominator=len(v))
                for name in metric_names for v in [[r[name] for r in selected if r[name] is not None]]}

    groups = defaultdict(list)
    for r in rows:
        for key in (f"mode:{r['mode']}", f"aspect:{r['aspect']}:{r['mode']}",
                    f"sentiment:{r['sentiment']}:{r['mode']}"):
            groups[key].append(r)
    paired = {}
    for name in metric_names:
        pairs = []
        for qid in queries:
            pair = {r['mode']: r[name] for r in rows if r['query_id'] == qid}
            if all(v is not None for v in pair.values()):
                pairs.append(pair['filtered'] - pair['unfiltered'])
        paired[name] = dict(filtered_minus_unfiltered=sum(pairs) / len(pairs) if pairs else None,
                            paired_query_denominator=len(pairs))
    return dict(status='human_confirmed' if confirmed else 'awaiting_human_review',
        run_id=run['run_id'], manifest_sha256=run['manifest_sha256'], label_version=LABEL_VERSION,
        rules=RULES, query_details=rows, groups={k: aggregate(v) for k, v in sorted(groups.items())},
        paired_comparison=paired, recall_at_5=None)

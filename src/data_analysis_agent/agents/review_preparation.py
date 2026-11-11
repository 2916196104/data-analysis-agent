"""P6-B 同事务输入准备；没有分类、数据库回补或跨进程恢复入口。"""
from dataclasses import dataclass, field
import json
import sqlite3
from uuid import uuid4

from data_analysis_agent.agents.review_multi_aspect import P6RequestGoal, check_current_ranking, validate_p6_plan
from data_analysis_agent.agents.review_plan import build_p6_query_plan
from data_analysis_agent.agents.review_topics import prepare_topic_input
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.opinions import ASPECTS

VERSION = 'review-p6-prepared-v1'


class P6PreparationError(ReviewDataError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__('历史排名已过期，需要刷新统计。' if diagnostics['reason'] == 'stale_ranking'
                         else 'P6 输入准备未通过完整性校验，尚未执行主题分类。')


def _validate_input(value, goal, snapshot):
    aspect = value['aspect']
    scope = {k: v for k, v in goal.expected_scope.items() if not k.startswith('baseline_')}
    rows, items, units = value['opinions'], value['items'], value['units']
    if (aspect not in goal.required_aspects or value['scope'] != goal.expected_scope
            or value['query_scope'] != {**scope, 'timezone': 'UTC'}
            or value['sentiment'] != 'negative' or type(value['total']) is not int
            or value['total'] != snapshot['negative_review_counts'][aspect]
            or not 0 < len(items) <= min(100, value['total'])
            or not 0 < len(rows) <= min(200, value['total_opinions'])
            or not 0 < len(units) <= 20 or len({r['review_id'] for r in items}) != len(items)
            or len({r['opinion_id'] for r in rows}) != len(rows)
            or len({r['opinion_id'] for r in units}) != len(units)):
        raise ValueError('冻结候选集合不完整。')
    by_review = {r['review_id']: r for r in items}
    by_unit = {r['opinion_id']: r for r in units}
    for row in rows:
        unit = by_unit[row['unit_id']]
        if (row['aspect'] != aspect or row['sentiment'] != 'negative'
                or unit['quote'] != row['quote'] or unit['aspect'] != aspect
                or not any(all(opinion.get(key) == row[key] for key in
                    ('opinion_id', 'quote', 'quote_start', 'quote_end', 'aspect', 'sentiment'))
                    for opinion in by_review[row['review_id']]['opinions'])):
            raise ValueError('冻结观点来源不完整。')
    data = {**value, 'scope': scope}
    if (value['summary']['successful_reviews'] != snapshot['denominator']['value']
            or {a:value['summary']['aspects'][a]['negative_review_count'] for a in ASPECTS} != snapshot['negative_review_counts']):
        raise ValueError('冻结统计摘要与快照不一致。')
    result = {'overview': {'kind':'context', 'result_id':'overview', 'parameters':{},
                          'data': {'scope':scope, 'summary':value['summary']}},
              'r1': {'kind': 'evidence', 'result_id': 'r1', 'parameters': {
        'product_ids': scope['product_ids'], 'aspect': aspect, 'sentiment': 'negative'}, 'data': data}}
    if validate_report(result, ['overview', 'r1'], sentiment_intent='negative', expected_scope=scope)['status'] != 'passed':
        raise ValueError('冻结原文或范围无效。')
    expected_coverage = {'selected_reviews': len(items), 'selected_opinions': len(rows), 'unique_expressions': len(units),
        'review_coverage': analytics.count_rate(len(items), value['total']),
        'opinion_coverage': analytics.count_rate(len(rows), value['total_opinions']),
        'truncated': len(items) < value['total'] or len(rows) < value['total_opinions']}
    if value['coverage'] != expected_coverage:
        raise ValueError('冻结覆盖率不一致。')


@dataclass(frozen=True)
class P6PreparedAnalysis:
    goal: P6RequestGoal
    payload_json: str = field(repr=False)
    payload_sha256: str

    def validate(self):
        try:
            payload = json.loads(self.payload_json)
            if (content_sha256(payload) != self.payload_sha256 or payload['version'] != VERSION
                    or payload['goal'] != self.goal.to_dict() or payload['status'] != 'ready'
                    or payload['plan'] != build_p6_query_plan(self.goal).to_dict()
                    or not check_current_ranking(self.goal, payload['current_snapshot'])
                    or payload['required_aspects'] != list(self.goal.required_aspects)
                    or [r['input']['aspect'] for r in payload['inputs']] != list(self.goal.required_aspects)):
                raise ValueError('冻结输入摘要或目标异常。')
            for row in payload['inputs']:
                if content_sha256(row['input']) != row['input_sha256']:
                    raise ValueError('维度输入摘要异常。')
                _validate_input(row['input'], self.goal, payload['current_snapshot'])
            return payload
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            raise ReviewDataError('P6 冻结输入缺失或校验失败，禁止重新查询数据库补齐。') from exc

    def get_input(self, aspect):
        """P6-C 唯一数据接口：只返回已封存副本，无连接、游标或数据库回补。"""
        payload = self.validate()
        if aspect not in self.goal.required_aspects:
            raise ReviewDataError('该维度不属于冻结的必做集合。')
        return next(row['input'] for row in payload['inputs'] if row['input']['aspect'] == aspect)

    def diagnostics(self):
        payload = self.validate()
        return {key: payload[key] for key in ('version', 'status', 'current_snapshot', 'snapshot_audit',
            'required_aspects', 'current_ranking_matches', 'query_executions', 'database_statement_count',
            'topic_model_calls')} | {'reason': 'classification_not_enabled', 'input_sha256': self.payload_sha256,
            'inputs': [{'aspect': row['input']['aspect'], 'total': row['input']['total'],
                'total_opinions': row['input']['total_opinions'], 'coverage': row['input']['coverage'],
                'input_sha256': row['input_sha256']} for row in payload['inputs']]}


def prepare_p6_analysis(connection, goal, plan, *, conversation_id, agent_identity, ranking_task, context=None):
    """调用者独占已开启的只读一致性事务；所有 analytics 共享此连接。"""
    diagnostics = {'version': VERSION, 'status': 'blocked', 'reason': 'invalid_plan',
        'required_aspects': list(goal.required_aspects), 'current_ranking_matches': False,
        'query_executions': 0, 'database_statement_count': 0, 'topic_model_calls': 0}
    statement_count = 0

    def trace(statement):
        nonlocal statement_count
        if statement.lstrip().upper().startswith(('SELECT', 'WITH')):
            statement_count += 1

    pg_start = getattr(connection, 'read_statement_count', 0)
    sqlite = isinstance(connection, sqlite3.Connection)
    if sqlite:
        connection.set_trace_callback(trace)
    try:
        check = validate_p6_plan(goal, goal.expected_scope, plan, conversation_id=conversation_id,
            agent_identity=agent_identity, ranking_task=ranking_task, context=context)
        if not check['passed']:
            raise ValueError('invalid_plan')
        diagnostics['reason'] = 'invalid_transaction'
        if not connection.in_transaction:
            raise ValueError('transaction_required')
        if sqlite:
            connection.execute('PRAGMA query_only = ON')
            isolation = 'sqlite_explicit_read_transaction'
        elif isinstance(connection, PostgresConnection):
            isolation = connection.execute('SHOW transaction_isolation').fetchone()[0]
            readonly = connection.execute('SHOW transaction_read_only').fetchone()[0]
            if isolation != 'repeatable read' or readonly != 'on':
                raise ValueError('repeatable_read_read_only_required')
        else:
            raise ValueError('unsupported_connection')
        audit = {'logical_transaction_id': uuid4().hex, 'read_started_at': utc_now(),
                 'backend': 'sqlite' if sqlite else 'postgresql', 'isolation': isolation, 'readonly': True}
        scope = {k: v for k, v in goal.expected_scope.items() if not k.startswith('baseline_')}
        diagnostics['reason'] = 'incomplete_current_statistics'
        diagnostics['query_executions'] += 1
        current = analytics.get_top_issues(connection, **scope)
        if (current['scope'] != {**scope, 'timezone': 'UTC'} or current.get('baseline') is not None
                or current['definitions'] != analytics.DEFINITIONS
                or set(current['current']['aspects']) != set(ASPECTS)
                or len(current['issues']) != len(ASPECTS)):
            raise ValueError('statistics_contract_mismatch')
        statistic = {'r1': {'kind': 'issues', 'result_id': 'r1', 'parameters': {'product_ids': scope['product_ids']}, 'data': current}}
        if validate_report(statistic, ['r1'], sentiment_intent='negative', expected_scope=scope)['status'] != 'passed':
            raise ValueError('invalid_current_statistics')
        snapshot = {'scope': goal.expected_scope,
            'definitions': current['definitions'],
            'denominator': {'field': current['definitions']['negative_rate_denominator'],
                            'value': current['current']['successful_reviews']},
            'negative_review_counts': {a: current['current']['aspects'][a]['negative_review_count'] for a in ASPECTS}}
        diagnostics.update(current_snapshot=snapshot, snapshot_audit=audit)
        diagnostics['current_ranking_matches'] = check_current_ranking(goal, snapshot)
        if not diagnostics['current_ranking_matches']:
            diagnostics['reason'] = 'stale_ranking'
            raise ValueError('refresh_required')
        frozen = []
        for aspect in goal.required_aspects:
            diagnostics['reason'] = 'incomplete_aspect_input'
            parameters = {'product_ids': scope['product_ids'], 'aspect': aspect, 'sentiment': 'negative'}
            plan.check_call('get_aspect_drilldown', parameters)
            diagnostics['query_executions'] += 1
            extracted = analytics.get_aspect_opinions(connection, **scope, aspect=aspect, sentiment='negative')
            if (extracted['scope'] != {**scope, 'timezone': 'UTC'} or extracted['aspect'] != aspect or extracted['sentiment'] != 'negative'
                    or extracted['summary'] != current['current']
                    or extracted['page'] != 1 or extracted['page_size'] != 100
                    or len(extracted['items']) != min(extracted['total'], 100)
                    or any(not item['opinions'] for item in extracted['items'])
                    or (extracted['total'] <= 100 and sum(len(item['opinions']) for item in extracted['items'])
                        != extracted['total_opinions'])):
                raise ValueError('aspect_snapshot_mismatch')
            value = {key: extracted[key] for key in ('aspect', 'sentiment', 'total', 'total_opinions')}
            value.update(prepare_topic_input(extracted), scope=goal.expected_scope, query_scope=extracted['scope'],
                summary=extracted['summary'],
                sampling={'method': 'latest_reviews_then_opinion_order; bounded_non_random',
                          'max_reviews': 100, 'max_unique_expressions': 20, 'max_opinions': 200,
                          'fetched_reviews': len(extracted['items'])}, snapshot_audit=dict(audit))
            value['coverage'] = {'selected_reviews': len(value['items']), 'selected_opinions': len(value['opinions']),
                'unique_expressions': len(value['units']),
                'review_coverage': analytics.count_rate(len(value['items']), value['total']),
                'opinion_coverage': analytics.count_rate(len(value['opinions']), value['total_opinions']),
                'truncated': len(value['items']) < value['total'] or len(value['opinions']) < value['total_opinions']}
            _validate_input(value, goal, snapshot)
            frozen.append({'input': value, 'input_sha256': content_sha256(value)})
        audit['read_finished_at'] = utc_now()
        diagnostics['database_statement_count'] = statement_count if sqlite else connection.read_statement_count - pg_start
        payload = {**diagnostics, 'status': 'ready', 'reason': 'classification_not_enabled',
                   'goal': goal.to_dict(), 'plan': plan.to_dict(), 'inputs': frozen}
        prepared = P6PreparedAnalysis(goal, canonical_json(payload), content_sha256(payload))
        prepared.validate()
        return prepared
    except Exception as exc:
        diagnostics['database_statement_count'] = statement_count if sqlite else getattr(connection, 'read_statement_count', 0) - pg_start
        raise P6PreparationError(diagnostics) from exc
    finally:
        if sqlite:
            connection.set_trace_callback(None)

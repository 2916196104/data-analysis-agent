"""Versioned full-snapshot topics, bounds and source tampering; no external models."""
from copy import deepcopy
import unittest

import test_review_dimension_content as fixture
from data_analysis_agent.agents.review_topic_statistics import build_statistics,validate_statistics,topic_request
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.reviews.analytics import get_topic_corpus


class TopicStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DimensionContentTests('test_scope_binding_and_simple_ranking_are_deterministic')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.scope={key:value for key,value in self.f.scope().items() if not key.startswith('baseline_')}

    def corpus(self):return get_topic_corpus(self.f.db,**self.scope)

    def test_native_full_topic_query_and_history_do_not_call_models(self):
        r=self.f.run_local('按细分主题统计差评')
        self.assertEqual(r['status'],'ok',r.get('error'))
        self.assertEqual(r['validation']['status'],'passed')
        self.assertEqual(r['total_model_calls'],0)
        d=r['results']['r1']['data']
        self.assertTrue(validate_statistics(d))
        self.assertEqual(d['coverage']['processed_opinions'],12)
        self.assertEqual(read_public_answer({'status':'succeeded','result':r}),r['public_answer'])
        self.assertIn('未判定',str(r['public_answer']))

    def test_synonyms_merge_and_counts_deduplicate_with_unknown_upper_bounds(self):
        corpus=self.corpus()
        items=corpus['items']
        # Replace synthetic opinions with explicit cooling equivalents, keeping source spans.
        for item,text in zip(items,['制冷不足；制冷不够给力','降温不给力；未知体验']):
            item['text']=text;item['opinions']=[]
            for quote in text.split('；'):
                start=text.index(quote)
                item['opinions'].append(dict(aspect='effectiveness',sentiment='negative',quote=quote,
                                            quote_start=start,quote_end=start+len(quote)))
        corpus['total_opinions']=4
        data=build_statistics(corpus,topic='cooling_poor')
        row=data['topics'][0]
        self.assertEqual((row['review_count'],row['opinion_count']),(2,3))
        self.assertEqual(row['rate'],.5)
        self.assertEqual(data['coverage']['unresolved_opinions'],1)
        self.assertEqual(data['coverage']['classification_coverage'],.75)
        self.assertTrue(validate_statistics(data))

    def test_unlabelled_and_unresolved_are_never_silently_negative(self):
        corpus=self.corpus()
        data=build_statistics(corpus,topic='cooling_poor')
        self.assertEqual(data['topics'][0]['review_count'],0)
        self.assertEqual(data['topics'][0]['rate_state'],'lower_bound')
        self.assertGreater(data['topics'][0]['upper_rate'],0)
        corpus['summary']['review_count']+=1
        data=build_statistics(corpus,topic='cooling_poor')
        self.assertEqual(data['coverage']['unlabelled_reviews'],1)
        self.assertGreater(data['topics'][0]['upper_rate'],0)

    def test_version_counts_coverage_spans_and_scope_tampering_rejected(self):
        data=build_statistics(self.corpus())
        for mutate in (lambda d:d.update(version='wrong'),lambda d:d['coverage'].update(classified_opinions=999),
                       lambda d:d['topics'][0].update(review_count=999),
                       lambda d:d['corpus']['items'][0]['opinions'][0].update(quote_start=999),
                       lambda d:d['corpus']['items'][0].update(product_id='outside')):
            forged=deepcopy(data);mutate(forged)
            self.assertFalse(validate_statistics(forged))

    def test_narrow_failure_and_intact_packaging_are_not_broadened_to_parent_themes(self):
        corpus=self.corpus()
        for item,text in zip(corpus['items'],['制冷不足','完全不制冷']):
            item['text']=text
            item['opinions']=[dict(aspect='effectiveness',sentiment='negative',quote=text,quote_start=0,quote_end=len(text))]
        corpus['total_opinions']=2
        self.assertEqual(build_statistics(corpus,topic='cooling_poor')['topics'][0]['review_count'],2)
        self.assertEqual(build_statistics(corpus,topic='cooling_failure')['topics'][0]['review_count'],1)
        self.assertEqual(topic_request('完全不制冷的反馈占全部评论多少？')['topic'],'cooling_failure')
        self.assertIsNone(topic_request('客服不耐烦的反馈占全部评论多少？'))
        from data_analysis_agent.agents.review_topic_statistics import fact
        self.assertEqual(fact('但是包装很结实'),'packaging_sturdy')
        self.assertEqual(fact('包装完好'),'packaging_good')

    def test_structured_topic_followups_keep_version_scope_and_denominator(self):
        first=self.f.run_local('按细分主题统计物流差评')
        self.assertEqual(first['status'],'ok',first.get('error'))
        context={'turns_read':1,'user':{'question':first['question']},'users':[{'question':first['question']}],
            'validated':{'scope':first['scope'],'sentiment':'negative','analysis_type':'topic_statistics',
                         'queries':[{'kind':'topic_statistics','parameters':first['results']['r1']['parameters']}]}}
        for question,aspect,sentiment in [('那好评呢','logistics','positive'),('那质量呢','quality','negative')]:
            follow=self.f.run_local(question,context=context)
            self.assertEqual(follow['status'],'ok',follow.get('error'))
            data=follow['results']['r1']['data']
            self.assertEqual((data['aspect'],data['sentiment'],data['denominator']),(aspect,sentiment,'review_count'))
            self.assertEqual(data['version'],first['results']['r1']['data']['version'])
            self.assertEqual(follow['scope'],first['scope'])

    def test_ranking_attaches_full_statistics_not_just_two_examples(self):
        r=self.f.run_local('差评主要集中在哪里？')
        self.assertEqual(r['status'],'ok',r.get('error'))
        d=r['results']['r1']['data']['topic_statistics']
        self.assertEqual(d['coverage']['processed_opinions'],12)
        self.assertIn('固定版本规则',str(r['public_answer']))

    def test_explicit_topic_rate_uses_all_reviews_and_cannot_become_causal(self):
        r=self.f.run_local('制冷不足的反馈占全部评论多少？')
        self.assertEqual(r['status'],'ok',r.get('error'))
        data=r['results']['r1']['data']
        self.assertEqual(data['topics'][0]['denominator'],4)
        self.assertIn('至少',str(r['public_answer']))
        self.assertIsNone(topic_request('为什么制冷不足，主要抱怨什么原因？'))
        self.assertIsNone(topic_request('按月查看细分主题趋势'))
        self.assertIsNone(topic_request('查看细分主题和数据质量'))

    def test_more_than_200_opinions_are_processed_and_pages_are_not_sampled(self):
        from unittest.mock import patch
        corpus=self.corpus();template=deepcopy(corpus['items'][0]);items=[]
        for i in range(205):
            item=deepcopy(template);item.update(review_id=1000+i,text='制冷不足')
            item['opinions']=[dict(aspect='effectiveness',sentiment='negative',quote='制冷不足',quote_start=0,quote_end=4)]
            items.append(item)
        calls=[]
        def page(db,*,page=1,**kwargs):
            calls.append(page)
            return dict(total=205,total_opinions=205,pages=3,items=deepcopy(items[(page-1)*100:page*100]))
        with patch('data_analysis_agent.reviews.analytics.get_evidence',side_effect=page):
            scanned=self.corpus()
        self.assertEqual(calls,[1,2,3]);self.assertEqual(len(scanned['items']),205)
        scanned['summary'].update(review_count=205,successful_reviews=205)
        data=build_statistics(scanned,topic='cooling_poor')
        self.assertEqual(data['coverage']['processed_opinions'],205)
        self.assertEqual(data['topics'][0]['review_count'],205)
        self.assertEqual(data['topics'][0]['rate_state'],'exact')

    def test_empty_window_positive_filter_and_explicit_denominator(self):
        empty=self.f.run_local('按细分主题统计差评',scope=self.f.scope(date_from='2030-01-01',date_to='2030-02-01'))
        self.assertEqual(empty['status'],'ok',empty.get('error'))
        self.assertIsNone(empty['results']['r1']['data']['topics'][0]['rate'])
        positive=self.f.run_local('按细分主题统计好评，以成功标注评论为分母')
        self.assertEqual(positive['status'],'ok',positive.get('error'))
        data=positive['results']['r1']['data']
        self.assertEqual(data['sentiment'],'positive')
        self.assertTrue(all(row['denominator_name']=='successful_reviews' for row in data['topics']))


if __name__=='__main__':unittest.main()

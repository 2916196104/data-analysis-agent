"""Synthetic fact and context controls for the two scoped timing intents."""

import unittest

from data_analysis_agent.agents import review_delivery_timing_evidence as timing
from test_review_reported_facts import evidence


DELIVERY_QUERY = '买家有没有抱怨迟迟收不到货？'
INSTALL_QUERY = '有没有安装师傅上门比较慢的评论？'


class DeliveryTimingEvidenceTests(unittest.TestCase):
    def test_explicit_current_non_receipt_supports_the_buyer_report(self):
        for text, quote in (
            ('到现在我还没收到货。', '到现在我还没收到货'),
            ('没收到货却被签收了，现在还没收到货。', '没收到货却被签收了，现在还没收到货'),
            ('包裹至今未送达。', '包裹至今未送达'),
        ):
            with self.subTest(text=text):
                result = timing.assess(DELIVERY_QUERY, [evidence(text, quote)])
                self.assertEqual(result['supported_target_fact_ids'], [timing.NOT_RECEIVED])
                self.assertEqual(result['permitted_keys'], ['synthetic-1'])

    def test_pending_installation_requires_receipt_and_waiting_inside_the_quote(self):
        for text in ('安装太慢，收货过了2天都没人上门安装。',
                     '货到了，现在还在等师傅上门安装。',
                     '已收货但还没上门安装。'):
            with self.subTest(text=text):
                self.assertEqual(timing.assess(INSTALL_QUERY, [evidence(text)])['supported_target_fact_ids'],
                                 [timing.INSTALL_PENDING])
        # The previous clause may establish delivery for the reader, but the cited span must also contain it.
        row = evidence('货到了，还在等师傅上门安装。', '还在等师傅上门安装')
        self.assertEqual(timing.assess(INSTALL_QUERY, [row])['permitted_keys'], [])

    def test_nearby_logistics_reports_cannot_replace_current_non_receipt(self):
        for text, quote in (
            ('物流很慢。', '物流很慢'),
            ('等了三天才送到。', '等了三天才送到'),
            ('退货寄回还没收到货。', '还没收到货'),
            ('上次还没收到货。', '还没收到货'),
            ('补发的包裹还没收到货。', '还没收到货'),
            ('现在还没收到快递短信。', '现在还没收到快递'),
            ('朋友说，现在还没收到货。', '现在还没收到货'),
            ('如果还没收到货就联系商家。', '还没收到货'),
            ('不是还没收到货。', '还没收到货'),
            ('还没收到货？', '还没收到货'),
            ('还没收到货。后来收到了。', '还没收到货'),
            ('现在还没收到货。货收到了。', '现在还没收到货'),
        ):
            with self.subTest(text=text):
                result = timing.assess(DELIVERY_QUERY, [evidence(text, quote)])
                self.assertEqual(result['permitted_keys'], [])

    def test_installation_delay_scope_blocks_attitude_completion_and_other_orders(self):
        for text, quote in (
            ('配送太慢，还没有人来安装。', '还没有人来安装'),
            ('安装师傅态度差。', '安装师傅态度差'),
            ('安装太慢。', '安装太慢'),
            ('货到了，我还没自己安装。', '货到了，我还没自己安装'),
            ('到货三天后才安排人来安装。', '到货三天后才安排人来安装'),
            ('朋友说，收货后没人来安装。', '收货后没人来安装'),
            ('如果收货后没人来安装就投诉。', '收货后没人来安装'),
            ('换货。收货后还没人来安装。', '收货后还没人来安装'),
            ('收货后还没人来安装。师傅现在已装好了。', '收货后还没人来安装'),
        ):
            with self.subTest(text=text):
                self.assertEqual(timing.assess(INSTALL_QUERY, [evidence(text, quote)])['permitted_keys'], [])

    def test_queries_distinguish_timing_from_other_outcomes(self):
        for text, fact in (
            ('有没有截至评价时仍未收到货的反馈？', timing.NOT_RECEIVED),
            ('有没有已经收货但仍未安装的评论？', timing.INSTALL_PENDING),
            ('有没有收货后一直在等待安装的反馈？', timing.INSTALL_PENDING),
            ('有没有安装上门慢的反馈？', timing.INSTALL_PENDING),
        ):
            with self.subTest(text=text):
                self.assertEqual(timing.target(text)['facts'], [fact])
        for text in ('有没有退货寄回很慢的反馈？', '有没有安装师傅态度差的评论？',
                     '有没有已经晚到但收到货的评论？', '有没有物流很慢的反馈？',
                     '有没有没收到货且客服态度差的评论？'):
            with self.subTest(text=text):
                self.assertIsNone(timing.target(text))

"""评论 CSV 的编码、字段、来源、证据和事务检查；仅操作临时库。"""

import csv
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.jobs.submission import prepare_parameters
from data_analysis_agent.reviews.store import connect_review_database, get_quality_summary, get_review_evidence, import_amazon_jsonl, init_database


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / 'test.db'
        init_database(self.db)

    def write_csv(self, rows, *, name='评论.CSV', encoding='utf-8-sig'):
        path = self.root / name
        with path.open('w', encoding=encoding, newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def row(self, **changes):
        return {'商品ID': '000123456789012345678', '用户ID': '00042', '评论内容': '好用，包装完整。\r\n客服回复很快。',
                '评论时间': '2024/8/21 17:30', '打分': '5', '商品名称': '模拟商品', '品牌': '模拟品牌', **changes}

    def test_encodings_multiline_text_evidence_and_cross_export_duplicates(self):
        for encoding in ('utf-8-sig', 'gb18030'):
            with self.subTest(encoding=encoding):
                path = self.write_csv([self.row()], name=encoding + '.csv', encoding=encoding)
                result = import_amazon_jsonl(self.db, reviews_path=path)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['imports'][0]['source'], 'reviews_csv')
                self.assertEqual(result['quality']['review_count'], 1)
        evidence = get_review_evidence(self.db, 1)
        self.assertEqual(evidence['text'], self.row()['评论内容'])
        self.assertEqual(json.loads(evidence['raw_json']), self.row())
        self.assertEqual(evidence['line_number'], 2)
        self.assertEqual(evidence['published_at'], '2024-08-21T17:30:00.000+00:00')
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            self.assertEqual(tuple(connection.execute('SELECT product_id, user_id FROM reviews').fetchone()),
                             (self.row()['商品ID'], '00042'))
            self.assertEqual(tuple(connection.execute('SELECT title, brand FROM products').fetchone()), ('模拟商品', '模拟品牌'))

    def test_english_headers_gb18030_native_id_conflicts_filter_and_limit(self):
        rows = [{'product_id': 'P1', 'review_id': 'R1', 'text': '好用', 'review_time': '2024-01-01T08:00:00+08:00', 'score': '4'},
                {'product_id': 'P2', 'review_id': 'R2', 'text': '一般', 'review_time': '2024-01-02', 'score': ''}]
        path = self.write_csv(rows, encoding='gb18030')
        limited = import_amazon_jsonl(self.db, reviews_path=path, max_reviews=1, product_ids=['P1'])
        self.assertTrue(limited['imports'][0]['limit_reached'])
        self.assertEqual(get_review_evidence(self.db, 1)['published_at'], '2024-01-01T00:00:00.000+00:00')
        continued = import_amazon_jsonl(self.db, reviews_path=path)
        self.assertEqual(continued['imports'][0]['duplicate_records'], 1)
        self.assertEqual(continued['quality']['review_count'], 2)
        rows[0]['text'] = '修改过的正文'
        conflict = import_amazon_jsonl(self.db, reviews_path=self.write_csv(rows))
        self.assertEqual(conflict['imports'][0]['invalid_records'], 1)
        self.assertEqual(get_review_evidence(self.db, 1)['text'], '好用')

    def test_bad_fields_and_row_lengths_keep_errors_and_valid_original_rows(self):
        rows = [self.row(), self.row(评论时间='不是日期'), self.row(打分='NaN'),
                self.row(商品ID='1.234E+18'), self.row(用户ID='1E+16'), self.row(商品ID=''),
                self.row(用户ID='another', 打分='', 评论内容='')]
        path = self.write_csv(rows)
        with path.open('a', encoding='utf-8') as handle:
            handle.write('too,few,columns\n')
        result = import_amazon_jsonl(self.db, reviews_path=path)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['imports'][0]['invalid_records'], 6)
        self.assertEqual(result['quality']['review_count'], 2)
        self.assertEqual(result['quality']['empty_text_count'], 1)

    def test_rejects_sales_missing_headers_bad_csv_and_mixed_metadata_without_writes(self):
        for text in ('user_id,auction_id,buy_mount,day\nU1,P1,2,20240101\n',
                     '商品ID,评论内容,评论时间,评论时间\nP1,好用,2024-01-01,2024-01-01\n',
                     '商品ID,评论内容,评论时间\nP1,"未闭合引号,2024-01-01\n'):
            path = self.root / 'bad.csv'
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(ReviewDataError):
                import_amazon_jsonl(self.db, reviews_path=path)
            self.assertEqual(get_quality_summary(self.db)['import_count'], 0)
        path = self.write_csv([self.row()])
        meta = self.root / 'meta.jsonl'
        meta.write_text('{"parent_asin":"P1"}\n', encoding='utf-8')
        with self.assertRaisesRegex(ReviewDataError, '不能与'):
            import_amazon_jsonl(self.db, reviews_path=path, meta_path=meta)
        with self.assertRaises(ReviewDataError):
            prepare_parameters('import', {'reviews_path': path.name, 'meta_path': meta.name}, import_root=self.root, env_file=None)
        with self.assertRaises(ReviewDataError):
            prepare_parameters('import', {'meta_path': path.name}, import_root=self.root, env_file=None)


if __name__ == '__main__':
    unittest.main()

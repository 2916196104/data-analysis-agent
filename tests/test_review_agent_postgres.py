"""四类扩展统计在 PostgreSQL 与 SQLite 下的结果及只读契约一致性。"""

from contextlib import closing
import os
import unittest

from data_analysis_agent.cli.migrate import migrate_sqlite
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.human_review import workbench_options
from data_analysis_agent.reviews.store import connect_review_database
import test_postgres as pg_fixture
import test_review_workbench as workbench_fixture


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_ADMIN_URL"), "requires isolated PostgreSQL")
class ReviewAgentPostgresTests(unittest.TestCase):
    new_database = pg_fixture.PostgresTests.new_database

    def setUp(self):
        self.admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
        self.target = self.new_database()
        self.fixture = workbench_fixture.WorkbenchStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        migrate_sqlite(self.fixture.db, self.target)
        self.scope = dict(**workbench_fixture.IDENTITY, product_ids=[], date_from="2023-01-01", date_to="2023-03-01")

    def test_extended_tools_and_dataset_summary_match_sqlite(self):
        for function, params in (
            (analytics.get_data_quality, {}),
            (analytics.get_aspect_trend, {"aspect": "packaging"}),
            (analytics.get_rating_distribution, {"group_by": "month"}),
            (analytics.compare_time_windows, {"baseline_from": "2022-12-01", "baseline_to": "2022-12-31"}),
        ):
            with self.subTest(function=function.__name__):
                self.assertEqual(function(self.target, **self.scope, **params), function(self.fixture.db, **self.scope, **params))
        self.assertEqual(workbench_options(self.target), workbench_options(self.fixture.db))

    def test_empty_and_unlabeled_denominators_match_sqlite(self):
        for changes in ({"model": "UNLABELED"}, {"date_from": "2024-01-01", "date_to": "2024-01-02"}):
            scope = {**self.scope, **changes}
            for function in (analytics.get_data_quality, analytics.get_aspect_trend, analytics.get_rating_distribution):
                with self.subTest(function=function.__name__, changes=changes):
                    self.assertEqual(function(self.target, **scope), function(self.fixture.db, **scope))

    def test_application_connection_uses_readonly_repeatable_snapshot(self):
        with closing(connect_review_database(self.target, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone()[0], "on")
            self.assertEqual(connection.execute("SHOW transaction_isolation").fetchone()[0], "repeatable read")


if __name__ == "__main__":
    unittest.main()

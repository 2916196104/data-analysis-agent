"""验证 SQL 日志保留执行语义，并且不泄露参数和驱动错误内容。"""

import io
import logging
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import psycopg
from psycopg.pq import TransactionStatus

from data_analysis_agent.cli.server import main
from data_analysis_agent.core.database import DatabaseError, PostgresConnection, configure_sql_logging
from data_analysis_agent.core.errors import ReviewDataError


LOGGER = "data_analysis_agent.sql"
TARGET = "postgresql://private-user:private-password@localhost/reviews"


class SqlLoggingTests(unittest.TestCase):
    def setUp(self):
        configure_sql_logging(False)
        self.addCleanup(configure_sql_logging, False)
        self.raw = MagicMock()
        self.raw.info.transaction_status = TransactionStatus.IDLE
        self.enterContext(patch("data_analysis_agent.core.database.psycopg.connect", return_value=self.raw))
        self.connection = PostgresConnection(TARGET, namespace="tasks")
        self.addCleanup(self.connection.close)
        self.raw.execute.reset_mock()

    def test_query_logs_template_and_duration_without_values(self):
        with self.assertLogs(LOGGER, level="INFO") as logs, patch(
                "data_analysis_agent.core.database.perf_counter", side_effect=[1, 1.012]):
            cursor = self.connection.execute("SELECT task_id\nFROM tasks WHERE task_id=?", ("private-task",))
        self.assertIs(cursor, self.raw.execute.return_value)
        self.raw.execute.assert_called_once_with("SELECT task_id\nFROM tasks WHERE task_id=%s", ("private-task",))
        output = "\n".join(logs.output)
        self.assertIn("SELECT task_id FROM tasks WHERE task_id=%s", output)
        self.assertIn("范围=tasks 耗时=12.00ms", output)
        self.assertNotIn("private-task", output)

    def test_connect_logs_identity_without_credentials(self):
        with self.assertLogs(LOGGER, level="INFO") as logs:
            connection = PostgresConnection(TARGET, readonly=True)
            connection.close()
        output = "\n".join(logs.output)
        self.assertIn("CONNECT postgresql://localhost:5432/reviews", output)
        self.assertIn("SET default_transaction_read_only = on", output)
        self.assertNotIn("private-user", output)
        self.assertNotIn("private-password", output)

    def test_failure_logs_type_without_driver_message(self):
        self.raw.execute.side_effect = psycopg.ProgrammingError("private-driver-message")
        with self.assertLogs(LOGGER, level="INFO") as logs, self.assertRaises(DatabaseError):
            self.connection.execute("SELECT * FROM missing WHERE id=?", ("private-value",))
        output = "\n".join(logs.output)
        self.assertIn("SQL 失败", output)
        self.assertIn("ProgrammingError", output)
        self.assertNotIn("private-driver-message", output)
        self.assertNotIn("private-value", output)

    def test_bulk_logging_does_not_consume_input_or_dump_rows(self):
        rows = iter([("private-one",), ("private-two",)])
        consumed = []
        self.raw.cursor.return_value.executemany.side_effect = lambda _query, values: consumed.extend(values)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            cursor = self.connection.executemany("INSERT INTO tasks(task_id) VALUES (?)", rows)
            self.connection.commit()
        self.assertIs(cursor, self.raw.cursor.return_value)
        self.assertEqual(consumed, [("private-one",), ("private-two",)])
        output = "\n".join(logs.output)
        self.assertIn("BEGIN", output)
        self.assertIn("EXECUTEMANY INSERT INTO tasks(task_id) VALUES (%s)", output)
        self.assertIn("COMMIT", output)
        self.assertNotIn("private-one", output)
        self.assertNotIn("private-two", output)

    def test_lock_and_rollback_are_logged(self):
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.rollback()
        output = "\n".join(logs.output)
        self.assertIn("| BEGIN", output)
        self.assertIn("SELECT pg_advisory_xact_lock(%s)", output)
        self.assertIn("ROLLBACK", output)
        self.assertNotIn("BEGIN IMMEDIATE", output)
        self.raw.rollback.assert_called_once()

    def test_disabled_logging_is_quiet_even_with_parent_info_enabled(self):
        with self.assertNoLogs("data_analysis_agent", level="INFO"):
            self.connection.execute("SELECT 1")
        configure_sql_logging(True)
        with self.assertLogs("data_analysis_agent", level="INFO"):
            self.connection.execute("SELECT 1")

    def test_server_reads_switch_from_selected_env_file(self):
        for value, enabled in (("1", True), ("false", False)):
            with self.subTest(value=value), patch("sys.argv", [
                "review-api", "serve", "--db", "unused.sqlite3", "--env-file", "selected.env",
            ]), patch("data_analysis_agent.cli.server.read_settings", return_value={
                "DATABASE_SQL_LOG": value,
            }) as read, patch("data_analysis_agent.cli.server.check_task_database",
                              side_effect=ReviewDataError("stop-before-startup")), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 1)
                self.assertEqual(str(read.call_args.args[0]), "selected.env")
                self.assertEqual(logging.getLogger(LOGGER).isEnabledFor(logging.INFO), enabled)


if __name__ == "__main__":
    unittest.main()

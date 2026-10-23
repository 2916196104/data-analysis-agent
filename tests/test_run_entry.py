"""统一开发启动器及其进程清理的检查。"""

import os
import signal
import socket
import subprocess
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

import run
from data_analysis_agent.core.config import ConfigurationError
from data_analysis_agent.dev_processes import ProcessFamily


class RunEntryTests(unittest.TestCase):
    def test_occupied_port_fails_before_spawning_or_opening_browser(self):
        listener = MagicMock()
        listener.__enter__.return_value = listener
        listener.bind.side_effect = OSError('occupied')
        with patch('run.socket.socket', return_value=listener), patch('run.ProcessFamily') as family, \
                patch('run.webbrowser.open') as browser:
            with self.assertRaisesRegex(RuntimeError, '已被占用'):
                run.launch()
            family.assert_not_called()
            browser.assert_not_called()

    def test_equal_ports_fail_before_spawning(self):
        with patch('run.ProcessFamily') as family:
            with self.assertRaisesRegex(RuntimeError, '不同端口'):
                run.launch(8000, 8000)
            family.assert_not_called()

    def test_missing_database_does_not_spawn_services(self):
        with patch('run.check_port'), patch('run.read_settings', return_value={}), patch('run.ProcessFamily') as family:
            with self.assertRaisesRegex(ConfigurationError, 'DATABASE_URL'):
                run.launch()
            family.assert_not_called()

    def test_open_browser_only_after_readiness_and_forward_custom_ports(self):
        events = []
        stop = MagicMock()
        stop.wait.return_value = True
        family = MagicMock()
        family.start.side_effect = [MagicMock(), MagicMock()]
        with patch('run.check_port'), patch('run.read_settings', return_value={'DATABASE_URL': 'postgresql://localhost/test'}), \
                patch('run.shutil.which', return_value='node'), patch('pathlib.Path.is_file', return_value=True), \
                patch('run.Event', return_value=stop), patch('run.ProcessFamily') as factory, \
                patch('run.wait_ready', side_effect=lambda *_: events.append('ready') or True), \
                patch('run.webbrowser.open', side_effect=lambda *_args, **_kwargs: events.append('browser') or True) as browser:
            factory.return_value.__enter__.return_value = family
            self.assertEqual(run.launch(18001, 15174), 0)
            browser.assert_called_once_with('http://127.0.0.1:15174', new=2)
            calls = family.start.call_args_list
            self.assertIn('18001', calls[0].args[0])
            self.assertIn('http://127.0.0.1:15174', calls[0].args[0])
            self.assertEqual(calls[1].kwargs['env']['VITE_API_BASE_URL'], 'http://127.0.0.1:18001/api/v1')
            factory.return_value.__exit__.assert_called_once()
        self.assertEqual(events, ['ready', 'browser'])

    def test_failed_start_does_not_open_browser_and_exits_context(self):
        with patch('run.check_port'), patch('run.read_settings', return_value={'DATABASE_URL': 'postgresql://localhost/test'}), \
                patch('run.shutil.which', return_value='node'), patch('pathlib.Path.is_file', return_value=True), \
                patch('run.ProcessFamily') as family, patch('run.wait_ready', side_effect=RuntimeError('startup failed')), \
                patch('run.webbrowser.open') as browser:
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                run.launch()
            browser.assert_not_called()
            family.return_value.__exit__.assert_called_once()

    def test_readiness_detects_child_exit_and_can_be_cancelled(self):
        process = MagicMock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, '后端已退出'):
            run.wait_ready([('后端', process)], ['http://unused', 'http://unused'], Event())
        stop = Event()
        stop.set()
        self.assertFalse(run.wait_ready([], [], stop))

    @unittest.skipUnless(os.name == 'nt', 'Windows job-object ownership')
    def test_failed_job_assignment_kills_suspended_child(self):
        family = object.__new__(ProcessFamily)
        family.processes = []
        family.job = MagicMock()
        family.job.assign_and_resume.side_effect = OSError('assignment failed')
        process = MagicMock()
        with patch('data_analysis_agent.dev_processes.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(OSError, 'assignment failed'):
                family.start(['python'], cwd='.', env={})
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows job-object ownership')
    def test_cleanup_closes_job_after_grace_period_even_for_hung_child(self):
        family = object.__new__(ProcessFamily)
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('server', 5), 0]
        family.processes = [process]
        family.job = MagicMock()
        family.close()
        process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
        family.job.close.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()

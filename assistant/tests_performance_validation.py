"""Acceptance-harness cleanup without launching inference or touching hardware."""

from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import call, patch

from django.test import SimpleTestCase

from scripts import validate_pi_performance as validation


class ValidationProcessCleanupTests(SimpleTestCase):
    def test_completed_regressions_do_not_signal_any_process(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validation.subprocess, "Popen") as popen, \
                patch.object(validation.os, "killpg", create=True) as killpg:
            popen.return_value.wait.return_value = 0
            result = validation.run_regressions(SimpleNamespace(expected_commit="a" * 40), Path(directory))
        self.assertEqual(result, 0)
        killpg.assert_not_called()
        popen.return_value.terminate.assert_not_called()
        popen.return_value.kill.assert_not_called()

    def test_linux_timeout_stops_only_owned_test_group_including_workers(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validation.sys, "platform", "linux"), \
                patch.object(validation.subprocess, "Popen") as popen, \
                patch.object(validation.os, "killpg", create=True) as killpg, \
                patch.object(validation.signal, "SIGKILL", 9, create=True):
            process = popen.return_value
            process.pid = 12345
            process.wait.side_effect = [subprocess.TimeoutExpired("audit-tests", 600), 0]
            with self.assertRaises(subprocess.TimeoutExpired):
                validation.run_regressions(SimpleNamespace(expected_commit="a" * 40), Path(directory))
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(killpg.call_args_list, [call(12345, signal.SIGTERM), call(12345, 9)])
            process.terminate.assert_not_called()

    def test_linux_interrupt_also_reaps_owned_test_group(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validation.sys, "platform", "linux"), \
                patch.object(validation.subprocess, "Popen") as popen, \
                patch.object(validation.os, "killpg", create=True) as killpg, \
                patch.object(validation.signal, "SIGKILL", 9, create=True):
            process = popen.return_value
            process.pid = 12345
            process.wait.side_effect = [KeyboardInterrupt(), 0]
            with self.assertRaises(KeyboardInterrupt):
                validation.run_regressions(SimpleNamespace(expected_commit="a" * 40), Path(directory))
            self.assertEqual(killpg.call_count, 2)
            self.assertEqual(process.wait.call_count, 2)

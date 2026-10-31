"""Complete applicable snapshot suite; no external network or production data."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--exclusions', type=Path)
    parser.add_argument('labels', nargs='*')
    args = parser.parse_args()
    root = args.root.resolve(); report = args.report.resolve()
    if args.exclusions:
        args.exclusions = args.exclusions.resolve()
    os.chdir(root); sys.path.insert(0, str(root))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    # Existing acceptance fixtures correctly refuse outputs inside source.
    temporary = Path(os.environ.get('COMPANION_TEST_TEMP_DIR', str(root.parent / (root.name + '-test-temporary')))).resolve()
    if temporary == root or root in temporary.parents:
        raise RuntimeError('Test temporary output must be outside the source directory.')
    temporary.mkdir(mode=0o700, exist_ok=True)
    tempfile.tempdir = str(temporary)
    os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
    def forbidden(*a, **k):
        raise OSError('External connections disabled during release verification.')
    socket.socket.connect = forbidden; socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    def no_hardware(event, arguments):
        if event == 'subprocess.Popen':
            executable = Path(str(arguments[0])).name.lower()
            if executable in {'sudo', 'systemctl', 'nmcli', 'aplay', 'arecord', 'piper', 'ollama', 'pinctrl', 'reboot'}:
                raise RuntimeError('Hardware/service command escaped its test mock: '+executable)
        if event == 'open' and isinstance(arguments[0], (str, bytes)):
            path = os.fsdecode(arguments[0])
            if path.startswith(('/dev/tty', '/dev/gpiochip', '/dev/snd/')):
                raise RuntimeError('Hardware file escaped its test mock.')
    sys.addaudithook(no_hardware)
    import django
    django.setup()
    from django.test.runner import DiscoverRunner
    from django.core.management import call_command
    excluded = json.loads(args.exclusions.read_text()) if args.exclusions else {}
    if os.name == 'nt':
        excluded['assistant.tests_pi_deployment_isolation.DeploymentIdempotencyTests.test_supervisor_detects_ancestor_and_equivalent_cherry_pick_without_services'] = 'POSIX deployment shell fixture cannot execute in this Windows environment.' 
    class Runner(DiscoverRunner):
        def build_suite(self, *a, **k):
            suite = super().build_suite(*a, **k)
            def flatten(item):
                if isinstance(item, unittest.TestSuite):
                    for child in item: yield from flatten(child)
                else: yield item
            chosen = []
            for test in flatten(suite):
                if test.id() in excluded:
                    print('Unavailable:', test.id(), excluded[test.id()])
                else: chosen.append(test)
            return self.test_suite(chosen)
        def suite_result(self, suite, result, **kwargs):
            data = {'tests_run': result.testsRun, 'failures': [t.id() for t, _ in result.failures],
                    'errors': [t.id() for t, _ in result.errors],
                    'skipped': [(t.id(), reason) for t, reason in result.skipped], 'unavailable': excluded}
            report.write_text(json.dumps(data, indent=2), encoding='utf-8')
            return super().suite_result(suite, result, **kwargs)
    call_command('check')
    failures = Runner(interactive=False, verbosity=1).run_tests(args.labels or ['.'])
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()

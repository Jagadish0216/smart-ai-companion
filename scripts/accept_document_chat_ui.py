"""One isolated acceptance entry point. No production database, storage or inference."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    'assistant.tests_physical_document_rag', 'assistant.tests_document_chat_ui', 'knowledge_base.tests_complete_knowledge', 'knowledge_base.tests_documents',
    'knowledge_base.tests_rag', 'knowledge_base.tests_retrieval_projection',
    'knowledge_base.tests_online_sync', 'assistant.tests_streaming',
    'assistant.tests_browser_voice', 'assistant.tests_routing',
    'assistant.tests_automatic_offline_rag', 'assistant.tests_online',
    'conversations.tests_memory', 'dashboard.tests',
    'assistant.tests_voice', 'assistant.tests_voice_loop', 'assistant.tests_voice_stream',
    'assistant.tests_execution_policy', 'assistant.tests_rag_v2',
]

def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def browser_acceptance():
    with tempfile.TemporaryDirectory(prefix='document-chat-acceptance-') as directory:
        logfile = Path(directory) / 'preview.log'
        with logfile.open('w', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, '-u', 'scripts/preview_document_chat_ui.py'], cwd=ROOT, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 90
                fixture = None
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError(logfile.read_text())
                    match = re.search(r'^FIXTURE_DIRECTORY=(.+)$', logfile.read_text(encoding='utf-8'), re.M)
                    if match:
                        fixture = Path(match[1].strip())
                        try:
                            with urllib.request.urlopen('http://127.0.0.1:8017/login/', timeout=2) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError('Fixture server did not become ready.\n' + logfile.read_text())
                run('node', 'scripts/test_document_chat_browser.cjs', 'http://127.0.0.1:8017', str(fixture / 'aurora.pdf'))
            finally:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(server.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Run the entire integrated regression suite.')
    parser.add_argument('--browser', action='store_true', help='Also run Playwright (requires node and playwright; CHROME_PATH may select installed Chrome).')
    parser.add_argument('--browser-only', action='store_true')
    args = parser.parse_args()
    if not args.browser_only:
        run(sys.executable, 'manage.py', 'test', *([] if args.full else TESTS), '--settings=config.test_settings', '--noinput')
        run(sys.executable, 'manage.py', 'check', '--settings=config.test_settings')
        run(sys.executable, 'manage.py', 'makemigrations', '--check', '--dry-run', '--settings=config.test_settings')
        for script in ('static/js/assistant-ui.js', 'static/js/assistant-documents.js', 'static/js/login.js', 'scripts/test_document_chat_browser.cjs'):
            run('node', '--check', script)
    if args.browser or args.browser_only:
        browser_acceptance()
    print('Acceptance checks passed.')

if __name__ == '__main__':
    main()

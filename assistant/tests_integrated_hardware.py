"""Protocol-level assurance only: no real board, model or acoustic claims."""
import sqlite3
import tempfile
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch
from django.test import SimpleTestCase
from assistant.display import DisplayState, DisplayMode
from assistant.display.ack_serial import AcknowledgedSerialDisplay
from assistant.display.queued_display import QueuedCompanionDisplay
from scripts.hardware_acceptance_support import snapshot_database, decode_throttle, compare_runs
from scripts.accept_integrated_hardware import assess


class Firmware:
    """Model the checked-in V2.2 newline parser and state caption reset."""
    def __init__(self, **kwargs):
        self.buffer=b'';self.state='ERROR';self.text="Let's try again";self.commands=[];self.closed=False
    def open(self): pass
    def close(self): self.closed=True
    @property
    def in_waiting(self): return len(self.buffer)
    def read(self,n):
        result=self.buffer[:n];self.buffer=self.buffer[n:];return result
    def write(self,payload):
        command=payload.decode().strip();self.commands.append(command)
        kind,_,value=command.partition(' ')
        if kind=='HELP': reply='STATE value TEXT value QUERY value MODE value LEVEL value'
        elif kind=='STATE': self.state=value;self.text='Ready' if value=='READY' else value;reply='STATE: '+value
        elif kind=='QUERY': reply='QUERY: OK'
        elif kind=='CLEAR': reply=value+': '+('Ready' if value=='TEXT' else 'cleared')
        elif kind=='TEXT': self.text=value;reply='TEXT: '+value
        else: reply=kind+': '+value
        self.buffer+=(reply+'\n').encode();return len(payload)


class DisplayIntegrationTests(SimpleTestCase):
    def bridge(self):
        self.firmware=Firmware()
        mocked=patch('assistant.display.serial_display.serial');self.serial=mocked.start();self.addCleanup(mocked.stop)
        self.serial.Serial.return_value=self.firmware
        display=AcknowledgedSerialDisplay(port='test-board',startup_delay_seconds=0,capture=True)
        self.addCleanup(display.close);return display

    def test_prior_disabled_display_leaves_error_but_wired_lifecycle_clears_it(self):
        display=self.bridge()
        # A valid terminal response alone has no effect on firmware state.
        terminal_answer='The indexed evidence describes prevention.'
        self.assertTrue(terminal_answer);self.assertEqual(self.firmware.text,"Let's try again")
        self.assertTrue(display.probe())
        queue=QueuedCompanionDisplay(display,probe=False)
        self.addCleanup(queue.close)
        queue.set_state(DisplayState.THINKING);queue.set_query('Explain the document')
        queue.set_mode(DisplayMode.KNOWLEDGE);queue.set_state(DisplayState.SPEAKING)
        queue.set_text(terminal_answer);self.assertTrue(queue.flush())
        self.assertEqual(self.firmware.text,terminal_answer[:40])
        queue.set_state(DisplayState.READY);self.assertTrue(queue.flush())
        self.assertEqual(self.firmware.text,'Ready')
        evidence=queue.evidence()
        self.assertEqual(evidence['counts']['written'],evidence['counts']['acknowledged'])
        self.assertEqual(evidence['rendering'],'unobserved')

    def test_protocol_sanitizes_query_and_status_control_characters(self):
        display=self.bridge();display.set_query('x\nSTATE ERROR\x00'+'y'*100);display.set_text('z'*90)
        self.assertEqual(len(self.firmware.commands),2)
        self.assertLessEqual(len(self.firmware.commands[0]),86)
        self.assertEqual(len(self.firmware.commands[1]),45)
        display.clear_text();self.assertEqual(display.evidence()['counts'].get('ack_timeout',0),0)

    def test_missing_ack_is_not_rendering_success(self):
        display=self.bridge();self.firmware.read=lambda n: b''
        display.set_state(DisplayState.READY)
        self.assertEqual(display.evidence()['counts']['ack_timeout'],1)
        self.assertFalse(display.probe())

    def test_audio_callback_does_not_wait_for_transport_and_queue_is_bounded(self):
        gate=Event();entered=Event()
        class Slow:
            enabled=True
            def set_state(self,value): entered.set();gate.wait(2);return True
            def __getattr__(self,name): return lambda *args: True
        queue=QueuedCompanionDisplay(Slow(),probe=False)
        try:
            queue.set_state(DisplayState.THINKING);self.assertTrue(entered.wait(1))
            started=time.monotonic()
            for n in range(100): queue.set_level(n%101);queue.set_text(str(n))
            queue.set_state(DisplayState.READY)
            self.assertLess(time.monotonic()-started,.2)
            self.assertLessEqual(len(queue._queue),32);self.assertGreater(queue.dropped,0)
            gate.set();self.assertTrue(queue.flush())
        finally: gate.set();queue.close()

    def test_reconnect_replays_latest_ready_instead_of_stale_error(self):
        class Offline:
            enabled=True
            def __init__(self): self.online=False;self.calls=[]
            def __getattr__(self,name):
                def call(*args):
                    if self.online: self.calls.append((name,args))
                    return self.online
                return call
        provider=Offline();queue=QueuedCompanionDisplay(provider,probe=False)
        try:
            queue.set_state(DisplayState.ERROR);self.assertFalse(queue.flush(.05))
            queue.set_state(DisplayState.READY);provider.online=True
            self.assertTrue(queue.flush(2))
            self.assertNotIn(('set_state',(DisplayState.ERROR,)),provider.calls)
        finally: queue.close()


class AcceptanceEvidenceTests(SimpleTestCase):
    def test_verified_snapshot_preserves_source_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            original=Path(directory)/'source.sqlite3';copy=Path(directory)/'copy.sqlite3'
            tables=['knowledge_base_document','knowledge_base_knowledgechunk','knowledge_base_evidenceversion','conversations_conversation','conversations_message','auth_user']
            with sqlite3.connect(original) as db:
                for table in tables: db.execute('CREATE TABLE '+table+' (id INTEGER)');db.execute('INSERT INTO '+table+' VALUES (1)')
            db.close()
            before=original.read_bytes();evidence=snapshot_database(original,copy)
            self.assertEqual(original.read_bytes(),before);self.assertEqual(evidence['integrity_check'],'ok')
            self.assertEqual(set(evidence['counts'].values()),{1})
            with self.assertRaises(ValueError): snapshot_database(original,original)

    def test_historical_temperature_flag_is_not_current_throttling(self):
        result=decode_throttle('throttled=0x80000')
        self.assertTrue(result['historical_soft_temperature_limit']);self.assertFalse(result['current_throttled'])
        self.assertFalse(result['current_soft_temperature_limit'])

    def test_partial_worker_cannot_pass_qwen_or_rag(self):
        report={'questions':['first','second'],'turns':[{'response':'answer','citations':[{'title':'doc'}]}],'worker_exit_code':1}
        self.assertEqual(assess(report)['qwen']['status'],'FAIL')
        self.assertEqual(assess(report)['rag']['status'],'FAIL')

    def test_comparison_rejects_simulation_and_different_turn_counts(self):
        self.assertEqual(compare_runs({'synthetic':True},{})['status'],'PARTIAL')
        self.assertEqual(compare_runs({'turns':[{}]},{'turns':[]})['status'],'PARTIAL')


    def test_service_ownership_restored_after_timeout_or_interrupt(self):
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from django.test import override_settings
        from scripts import accept_integrated_hardware as tool
        for failure in (subprocess.TimeoutExpired('worker',30),KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                output=Path(directory)/'report.json'
                process=MagicMock(pid=1234,returncode=0)
                process.wait.side_effect=[failure,0]
                with override_settings(AI_ENGINE='local',OLLAMA_MODEL='qwen3.5:4b-q4_K_M',TTS_ENGINE='piper',COMPANION_DISPLAY_ENABLED=True,COMPANION_DISPLAY_PORT='fake'), patch.object(tool.sys,'platform','linux'), patch.object(tool.sys,'argv',['accept','--question','first','--question','second','--output',str(output),'--manage-services']), patch.object(tool,'load_environment'), patch.object(tool,'snapshot_database',return_value={'status':'PASS'}), patch.object(tool,'serial_owners',return_value={'status':'PASS','pids':[]}), patch.object(tool.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run, patch.object(tool.subprocess,'Popen',return_value=process), patch.object(tool.os,'killpg',create=True) as kill:
                    self.assertEqual(tool.main(),1)
                    self.assertTrue(output.exists())
                    self.assertIn((['sudo','systemctl','start','smart-ai-companion-voice.service'],),[call.args for call in run.call_args_list])
                    import json
                    self.assertEqual(json.loads(output.read_text()).get('error_type'),type(failure).__name__)
                    kill.assert_called_once()


    def test_runtime_diagnostics_preserve_configured_controls_without_prompt_text(self):
        from assistant.ai_engine.local import LocalLLMEngine
        from assistant.services import _safe_ollama_metrics
        engine=LocalLLMEngine()
        controls=engine.runtime_diagnostics(num_predict=64)
        self.assertEqual(controls['requested_num_predict'],64)
        self.assertEqual(controls['num_ctx'],engine._num_ctx)
        self.assertEqual(controls['think_enabled'],engine._think)
        safe=_safe_ollama_metrics(dict(controls,thinking_chars=27,thinking='PRIVATE HIDDEN TEXT'))
        self.assertEqual(safe['thinking_chars'],27)
        self.assertNotIn('thinking',safe)

    def test_protocol_model_matches_checked_in_firmware_contract(self):
        root=Path(__file__).resolve().parents[1]/'hardware'/'esp32'/'companion_display'
        source=(root/'companion_display.ino').read_text()
        for literal in ('QUERY: OK','STATE: ','MODE: ','LEVEL: ','TEXT: '):
            self.assertIn(literal,source)
        config=(root/'display_config.h').read_text()
        self.assertIn('MAX_QUERY_TEXT_LENGTH = 80',config)
        self.assertIn('MAX_STATUS_TEXT_LENGTH = 40',config)

# Reuse fixture setup helpers without inheriting/discovering its test methods.
from django.test import TestCase, override_settings
from assistant import tests_physical_document_rag as document_fixtures


@override_settings(AI_ENGINE='local',RAG_ENABLED=True,ONLINE_RETRIEVAL_ENABLED=False,PHYSICAL_VOICE_DOCUMENTS_ENABLED=True,KNOWLEDGE_REQUIRE_EVIDENCE=True)
class VoiceDisplayPipelineTests(TestCase):
    setUp=document_fixtures.PhysicalDocumentTests.setUp
    upload=document_fixtures.PhysicalDocumentTests.upload

    def test_two_real_sqlite_voice_turns_share_grounding_and_serial_spoken_captions(self):
        from scripts import voice_loop
        from assistant.voice.tts import MockTTSProvider
        from conversations.models import Message
        document=self.upload();firmware=Firmware();provider=MockTTSProvider();observed=[]
        with patch('assistant.display.serial_display.serial') as serial, patch('scripts.voice_loop.play_audio') as playback, patch('scripts.voice_loop.get_tts_provider',return_value=provider):
            serial.Serial.return_value=firmware
            transport=AcknowledgedSerialDisplay(port='test-board',startup_delay_seconds=0,capture=True)
            queue=QueuedCompanionDisplay(transport)
            try:
                conversation=None
                for question in ('Explain the second objective from my project report.','In that same PDF explain the second objective.'):
                    start=len(firmware.commands)
                    conversation=voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig(''),conversation,display=queue,text=question,on_result=observed.append)
                    self.assertTrue(queue.flush())
                    commands=firmware.commands[start:]
                    for command in ('STATE THINKING','MODE KNOWLEDGE','STATE SPEAKING','STATE READY'):
                        self.assertIn(command,commands)
                    answer=Message.objects.filter(conversation_id=conversation,sender='AI').last()
                    self.assertEqual(answer.source_metadata['rag_sources'][0]['document_id'],document.pk)
                    self.assertEqual(answer.source_metadata['rag_sources'][0]['page_number'],1)
                    self.assertTrue(any(command.startswith('TEXT ') and command[5:] in answer.text for command in commands))
                    self.assertEqual(firmware.state,'READY')
                self.assertEqual(len(observed),2)
                self.assertGreaterEqual(playback.call_count,2)
                self.assertEqual(queue.evidence()['counts'].get('ack_timeout',0),0)
            finally: queue.close()

"""Deterministic regressions; no acoustic, speedup or hardware-duplex claims."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from threading import Event
from unittest.mock import MagicMock, patch
import wave

from django.test import SimpleTestCase
from assistant import latency_trace as tracing, tests_voice_stream as fixtures
from assistant.tests_hands_free import pcm
from assistant.voice.diagnostics import VoiceDiagnostics, FIELDS
from assistant.voice.hands_free import HandsFreeEndpointDetector, HandsFreeCaptureConfig, record_audio_hands_free
from assistant.voice.interruption import InterruptControl, VoiceInterrupted, delivery
from assistant.voice.processes import AudioCancelledError
from assistant.voice.wakeword.base import WakeWordConfig
from assistant.voice.wakeword.openwakeword_provider import OpenWakeWordProvider
from assistant.tests_wakeword import native_pcm
from scripts import voice_loop


class EndpointRefinementTests(SimpleTestCase):
    def detector(self, floor=.1):
        return HandsFreeEndpointDetector(floor, .9, relative=True, noise_relative=True)

    def feed(self, detector, level, frames):
        return [detector.feed(level, .02) for _ in range(frames)]

    def test_fan_above_old_ceiling_no_longer_prevents_quiet_completion(self):
        old = HandsFreeEndpointDetector(.1, .9, relative=True)
        new = self.detector()
        for detector in (old, new):
            self.feed(detector, .22, 25)
        self.assertFalse(any(self.feed(old, .1, 50)))
        self.assertTrue(any(self.feed(new, .1, 50)))

    def test_low_volume_question_is_evidence_without_changing_gain(self):
        detector = self.detector(.001)
        self.feed(detector, .004, 25)
        self.assertTrue(any(self.feed(detector, .001, 45)))

    def test_mid_sentence_pause_and_brief_silence_resume_without_endpoint(self):
        for pause in (10, 35, 44):
            detector = self.detector()
            self.feed(detector, .22, 25)
            self.assertFalse(any(self.feed(detector, .1, pause)))
            self.assertFalse(any(self.feed(detector, .16, 20)))
            self.assertEqual(detector.quiet_seconds, 0)
            self.assertTrue(any(self.feed(detector, .1, 45)))

    def test_long_question_no_speech_and_single_noise_peak_remain_bounded_fallbacks(self):
        for values in ([.22] * 500, [.1] * 500, [.3] + [.1] * 499):
            detector = self.detector()
            self.assertFalse(any(detector.feed(value, .02) for value in values))

    def test_question_wav_keeps_preroll_and_final_quiet_and_releases_reader(self):
        prefix = pcm(.01, frames=480)
        chunks = [pcm(.22)] * 25 + [pcm(.1)] * 45
        config = HandsFreeCaptureConfig(max_seconds=10, noise_relative_endpoint=True)
        with tempfile.TemporaryDirectory() as directory, patch('assistant.voice.hands_free.NativeRequestReader') as factory:
            reader = factory.return_value
            reader.read_frames.side_effect = chunks
            path = Path(directory) / 'request.wav'
            record_audio_hands_free(path, 'hw:test', config, immediate=True,
                                   initial_audio=prefix, background_rms=.1)
            with wave.open(str(path)) as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), prefix + b''.join(chunks))
            reader.close.assert_called_once()
            self.assertEqual(reader.read_frames.call_count, 70)

    def test_capture_failure_releases_reader_in_refined_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch('assistant.voice.hands_free.NativeRequestReader') as factory:
            factory.return_value.read_frames.side_effect = OSError('capture unavailable')
            with self.assertRaisesRegex(Exception, 'capture unavailable'):
                record_audio_hands_free(Path(directory) / 'request.wav', 'hw:test',
                                       HandsFreeCaptureConfig(noise_relative_endpoint=True), immediate=True, background_rms=.1)
            factory.return_value.close.assert_called_once()


class DiagnosticRefinementTests(SimpleTestCase):
    def test_live_monitor_counters_separate_scores_guard_and_confirmation(self):
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        provider.model = MagicMock()
        provider.model.predict.side_effect = [{'hey_jarvis': score} for score in (.7, .9, .9, .95)]
        capture = MagicMock()
        capture.chunks.return_value = iter([native_pcm()] * 4)
        summary = VoiceDiagnostics(True)
        guard = MagicMock(side_effect=[False, True, True])
        with patch('assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture', return_value=capture):
            result = provider.wait_for_wake_word('mic', guard=guard, threshold=.85,
                                                 confirmations=2, diagnostics=summary)
        self.assertTrue(result.detected)
        self.assertEqual(summary.values['frames'], 4)
        self.assertEqual(summary.values['max_score'], .95)
        self.assertEqual(summary.values['confirmations'], 2)
        self.assertEqual(summary.values['echo_rejections'], 1)
        self.assertTrue(summary.values['monitor_ready'])
        capture.close.assert_called_once()

    def test_failed_duplex_start_is_visible_and_keeps_keyboard_fallback(self):
        provider = MagicMock()
        provider.wait_for_wake_word.side_effect = OSError('device busy')
        control = InterruptControl(provider=provider, voice=True, keyboard=False, diagnostics=True)
        with patch('sys.stderr', new_callable=io.StringIO), redirect_stdout(io.StringIO()):
            control.reference(b'\0' * 7680)
            control.close()
        self.assertTrue(control.diagnostics.values['monitor_failed'])
        self.assertFalse(control.interrupted.is_set())
        self.assertFalse(control.diagnostics.values['reference_ready'])

    def test_diagnostics_are_opt_in_bounded_and_cannot_contain_private_content(self):
        for enabled in (False, True):
            summary = VoiceDiagnostics(enabled)
            summary.record('PRIVATE_PROMPT', 1)
            summary.record('max_score', 'PRIVATE_SPEECH')
            summary.record('frames', float('inf'))
            for i in range(1000):
                summary.record('max_score', i / 1000, maximum=True)
                summary.increment('frames')
            self.assertLessEqual(len(summary.values), len(FIELDS))
            with redirect_stdout(io.StringIO()) as output:
                summary.report()
            self.assertNotIn('PRIVATE', output.getvalue())
            if enabled:
                values = json.loads(output.getvalue().split(': ', 1)[1])
                self.assertEqual(values['frames'], 1000)
            else:
                self.assertEqual(output.getvalue(), '')

    def test_player_signal_precedes_http_shutdown_and_cleanup_is_acknowledged(self):
        fixture = fixtures.StreamingAudioTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        config = replace(fixture.config, audio_backend='alsa', voice_refinements=True)
        control = InterruptControl(keyboard=False, diagnostics=True)
        process = fixture.player.process
        process.poll.return_value = None
        order = []
        process.terminate.side_effect = lambda: order.append('player')
        control.register(lambda: order.append('http'))
        def interrupt(*args, **kwargs):
            control.cancel()
            self.assertEqual(order, ['player', 'http'])
            raise AudioCancelledError('cancelled')
        fixture.stream_write.side_effect = interrupt
        fixture.player.abort.side_effect = lambda: setattr(process.poll, 'return_value', 0)
        with delivery(control), self.assertRaises(VoiceInterrupted), redirect_stdout(io.StringIO()):
            fixture.speak(fixtures.stream_events(fixtures.FIRST_SENTENCE + ' ' + fixtures.SECOND_SENTENCE), config)
        self.assertTrue(control.diagnostics.values['output_reaped'])
        self.assertIn('cancel_ack_ms', control.diagnostics.values)
        self.assertEqual(len(control.callbacks), 1) # HTTP callback only; player unregistered.

    def test_refined_stream_order_and_per_unit_timing_have_no_content(self):
        fixture = fixtures.StreamingAudioTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        config = replace(fixture.config, voice_refinements=True)
        text = fixtures.FIRST_SENTENCE + ' ' + fixtures.SECOND_SENTENCE
        trace = tracing.Trace(synthetic=True)
        with tracing.cycle(trace), redirect_stdout(io.StringIO()):
            fixture.speak(fixtures.stream_events(text), config)
        spoken = [call.args[0] for call in fixture.provider.synthesize.call_args_list]
        self.assertEqual(' '.join(spoken), text)
        units = [e for e in trace.events if e['name'] == 'audio_unit_ready']
        self.assertEqual([e['unit'] for e in units], list(range(1, len(units) + 1)))
        self.assertTrue(all('tts_ms' in e and 'prepare_ms' in e for e in units))
        self.assertNotIn(fixtures.FIRST_SENTENCE, json.dumps(trace.report()))


class SentenceRefinementTests(SimpleTestCase):
    def test_punctuation_and_retained_quiet_do_not_create_artificial_mid_phrase_gaps(self):
        frames = voice_loop.refined_pause_frames
        self.assertEqual(frames('continuous words', 0, 0), 0)
        self.assertEqual(frames('a clause,', 0, 0), 16800)
        self.assertEqual(frames('a sentence.', 0, 0), 8640)
        self.assertEqual(frames('a sentence.', 4800, 4800), 0)
        self.assertEqual(frames('a clause,', 0, 0, .5), 0)

    def test_prebuffer_does_not_wait_for_response_completion(self):
        fixture = fixtures.StreamingAudioTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        played = Event()
        fixture.play_audio.side_effect = lambda *args, **kwargs: played.set()
        text = fixtures.FIRST_SENTENCE + ' ' + fixtures.SECOND_SENTENCE
        def events():
            yield {'type': 'start', 'conversation_id': 42}
            yield {'type': 'delta', 'text': fixtures.FIRST_SENTENCE + ' '}
            self.assertTrue(played.wait(2), 'Prebuffer waited for EOF instead of its bounded deadline')
            yield {'type': 'delta', 'text': fixtures.SECOND_SENTENCE}
            yield {'type': 'done', 'conversation_id': 42, 'response': text}
        with redirect_stdout(io.StringIO()):
            fixture.speak(events(), replace(fixture.config, voice_refinements=True))
        self.assertEqual(fixture.timings['playback_chunks'], 2)

    def test_complete_later_sentence_released_without_waiting_for_120_chars(self):
        sentence = 'The local processor coordinates the microphone and the speaker safely.'
        old = voice_loop.StreamingSpeechBuffer()
        new = voice_loop.StreamingSpeechBuffer()
        new.release_complete_sentences = True
        for buffer in (old, new):
            self.assertTrue(buffer.feed(fixtures.FIRST_SENTENCE + ' '))
        self.assertEqual(old.feed(sentence + ' '), [])
        self.assertEqual(new.feed(sentence + ' '), [sentence])

    def test_delta_splits_preserve_every_word_in_order(self):
        text = fixtures.FIRST_SENTENCE + ' ' + fixtures.SECOND_SENTENCE + ' ' + fixtures.THIRD_SENTENCE
        for width in (1, 7, 31, 170):
            buffer = voice_loop.StreamingSpeechBuffer()
            buffer.release_complete_sentences = True
            units = []
            for i in range(0, len(text), width):
                units.extend(buffer.feed(text[i:i + width]))
            units.extend(buffer.feed('', final=True))
            self.assertEqual(' '.join(units), text)
            self.assertLessEqual(len(buffer.pending), 220)

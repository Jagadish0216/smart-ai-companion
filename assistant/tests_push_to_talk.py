"""No GPIO/audio hardware: retain PCM and stop at release, not VAD silence."""
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import tempfile
import wave
import itertools
from django.test import SimpleTestCase
from assistant.tests_hands_free import pcm
from assistant.voice.hands_free import NativeRequestReader
from assistant.voice.push_to_talk import ShortPress, TalkButton, record_held
from scripts import voice_loop


class PushToTalkTests(SimpleTestCase):
    def test_unconfirmed_capture_cleanup_is_fatal(self):
        from assistant.voice.interruption import InterruptionCleanupError
        held=Event();held.set()
        with tempfile.TemporaryDirectory() as directory, patch('assistant.voice.push_to_talk.NativeRequestReader') as factory:
            factory.return_value.read_frames.side_effect=lambda *a,**k:held.clear()
            factory.return_value.close.side_effect=TimeoutError('unreaped')
            with self.assertRaises(InterruptionCleanupError):record_held(Path(directory)/'q.wav','mic',held)

    def test_short_contact_bounce_does_not_become_a_stable_press(self):
        button=object.__new__(TalkButton);button.gpio=MagicMock();button.handle=1
        button.gpio.gpio_read.side_effect=[0,0,1]+[1]*100
        clock=itertools.count(0,.005)
        with patch('assistant.voice.push_to_talk.time.monotonic',side_effect=lambda:next(clock)), patch('assistant.voice.push_to_talk.time.sleep'):
            self.assertFalse(button.wait(.1))

    def test_ptt_cycle_uses_existing_stt_service_context_and_tft_without_fixed_or_vad_capture(self):
        held=Event();held.set()
        states=[]
        config=voice_loop.VoiceLoopConfig('source',audio_backend='alsa')
        def capture(path,device,event,**kwargs):kwargs['on_ready']()
        def speech(events,*args,**kwargs):
            list(events)
            kwargs['on_playback_start']('Public answer.')
            return 42,{}
        with patch('assistant.voice.push_to_talk.record_held',side_effect=capture) as record, \
             patch('scripts.voice_loop.record_audio_alsa') as fixed, \
             patch('scripts.voice_loop.record_audio_with_capture_vad') as vad, \
             patch('scripts.voice_loop.normalize_audio'), \
             patch('scripts.voice_loop.get_stt_provider') as stt, \
             patch('scripts.voice_loop.AssistantService.process_message_stream',return_value=[]) as service, \
             patch('scripts.voice_loop.speak_stream_response',side_effect=speech), \
             patch('scripts.voice_loop.get_tts_provider'), \
             patch('scripts.voice_loop._set_display_state',side_effect=lambda display,state:states.append(state)):
            stt.return_value.transcribe.return_value='What is Smart AI Companion?'
            result=voice_loop.run_voice_cycle(config,17,display=None,talk_button=SimpleNamespace(held=held))
        self.assertEqual(result,42)
        self.assertEqual(service.call_args.kwargs['conversation_id'],17)
        record.assert_called_once();fixed.assert_not_called();vad.assert_not_called()
        self.assertEqual(states[0],voice_loop.DisplayState.LISTENING)
        self.assertIn(voice_loop.DisplayState.THINKING,states)
        self.assertIn(voice_loop.DisplayState.SPEAKING,states)
        self.assertEqual(states[-1],voice_loop.DisplayState.READY)

    def test_release_retains_first_last_words_and_does_not_read_again(self):
        held = Event(); held.set()
        chunks = [pcm(.05)] * 8
        calls = []
        def read(*args, **kwargs):
            calls.append(args)
            self.assertTrue(kwargs['keep_recording']())
            if len(calls) == len(chunks):held.clear()
            return chunks[len(calls)-1]
        with tempfile.TemporaryDirectory() as directory, patch('assistant.voice.push_to_talk.NativeRequestReader') as factory:
            reader = factory.return_value; reader.read_frames.side_effect = read
            output = Path(directory) / 'question.wav'
            ready = MagicMock()
            record_held(output, 'mic', held, on_ready=ready)
            with wave.open(str(output)) as audio:
                self.assertEqual(audio.readframes(audio.getnframes()), b''.join(chunks))
            self.assertEqual(len(calls), 8)
            ready.assert_called_once()
            reader.close.assert_called_once()

    def test_short_press_and_shutdown_always_close_owned_reader(self):
        for failure in (None, KeyboardInterrupt(), OSError('device unavailable')):
            held = Event();held.set()
            with tempfile.TemporaryDirectory() as directory, patch('assistant.voice.push_to_talk.NativeRequestReader') as factory:
                factory.return_value.read_frames.side_effect = failure if failure else lambda *a, **k: held.clear()
                expected = type(failure) if failure else ShortPress
                with self.assertRaises(expected):record_held(Path(directory)/'q.wav','mic',held)
                factory.return_value.close.assert_called_once()

    def test_stuck_button_has_maximum_duration(self):
        held=Event();held.set()
        with tempfile.TemporaryDirectory() as directory, patch('assistant.voice.push_to_talk.NativeRequestReader') as factory:
            factory.return_value.read_frames.return_value=pcm(.05)
            record_held(Path(directory)/'q.wav','mic',held,max_seconds=1)
            self.assertEqual(factory.return_value.read_frames.call_count,50)
            factory.return_value.close.assert_called_once()

    def test_release_interrupts_stalled_native_read_before_selector_wait(self):
        reader=NativeRequestReader('mic',20)
        reader.selector=MagicMock()
        self.assertEqual(reader.read_frames(960,keep_recording=lambda:False),b'')
        reader.selector.select.assert_not_called()

    def test_missing_chip_or_claimed_line_fails_before_capture(self):
        gpio=MagicMock();gpio.error=OSError
        gpio.gpio_get_chip_info.return_value=(54,'gpiochip0','pinctrl-rp1')
        gpio.gpio_get_mode.return_value=1
        with patch.dict('sys.modules',{'lgpio':gpio}), patch('assistant.voice.push_to_talk.Path.glob',return_value=[Path('/dev/gpiochip0')]):
            with self.assertRaisesRegex(RuntimeError,'already claimed'):TalkButton()
        gpio.gpio_claim_input.assert_not_called()
        gpio.gpiochip_close.assert_called_once()

    def test_consecutive_cycles_preserve_context_and_ignore_busy_presses(self):
        button=SimpleNamespace(held=Event())
        captured=[]
        def cycle(config,conversation_id,**kwargs):
            captured.append(conversation_id)
            if len(captured)==3:raise KeyboardInterrupt
            return 42
        # Mock the two human transitions; no separate microphone worker exists.
        with patch('assistant.voice.push_to_talk.wait_released'), patch.object(button.held,'wait',return_value=True), \
             patch('scripts.voice_loop.run_voice_cycle',side_effect=cycle):
            with self.assertRaises(KeyboardInterrupt):voice_loop.run_push_to_talk(voice_loop.VoiceLoopConfig('source'),button,None)
        self.assertEqual(captured,[None,42,42])

    def test_press_and_release_debouncing_and_active_low(self):
        gpio=MagicMock();gpio.error=OSError
        gpio.gpio_get_chip_info.return_value=(54,'gpiochip0','pinctrl-rp1')
        gpio.gpio_get_mode.return_value=0
        gpio.gpio_read.return_value=0
        with patch.dict('sys.modules',{'lgpio':gpio}), patch('assistant.voice.push_to_talk.Path.glob',return_value=[Path('/dev/gpiochip0')]):
            button=TalkButton()
            self.assertTrue(button.is_set())
            gpio.gpio_claim_input.assert_called_once_with(gpio.gpiochip_open.return_value,17,gpio.SET_PULL_UP)
            with patch('assistant.voice.push_to_talk.time.monotonic',side_effect=[0,.001,.002,.043,.044]), patch('assistant.voice.push_to_talk.time.sleep'):
                self.assertTrue(button.wait(.1))
            gpio.gpio_read.return_value=1
            self.assertFalse(button.is_set())
            button.close()

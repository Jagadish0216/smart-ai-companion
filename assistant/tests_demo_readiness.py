"""Step 51 public amplitude fixtures: simulation only, never acoustic acceptance."""
import struct
from django.test import SimpleTestCase
from assistant.voice.capture_vad import AdaptiveCaptureVAD
from scripts.voice_loop import _normalized_alsa_left_rms


class DemoAudioFixtures(SimpleTestCase):
    def run_fixture(self, segments):
        vad = AdaptiveCaptureVAD()
        events = []
        for rms, frames in segments:
            for _ in range(frames):
                events.append(vad.feed(rms, .02))
                if vad.stop_reason:
                    return vad, events
        return vad, events

    def test_quiet_speech_after_quiet_calibration(self):
        vad, events = self.run_fixture([(0.001,25),(.025,20),(.001,40)])
        self.assertTrue(vad.accepted)
        self.assertEqual(vad.stop_reason,"silence")
        self.assertIn("start",events)

    def test_normal_speech_after_cue(self):
        vad, _ = self.run_fixture([(.01,25),(.06,20),(.005,40)])
        self.assertTrue(vad.accepted)
        self.assertEqual(vad.stop_reason,"silence")

    def test_background_noise_never_becomes_speech(self):
        vad, _ = self.run_fixture([(.015,250)])
        self.assertFalse(vad.accepted)
        self.assertEqual(vad.stop_reason,"start_timeout")

    def test_immediate_post_calibration_speech_is_detected(self):
        vad, events = self.run_fixture([(.005,25),(.06,3)])
        self.assertEqual(events[27],"start")
        self.assertTrue(vad.speech_started)

    def test_speaking_during_calibration_can_poison_floor(self):
        vad, _ = self.run_fixture([(.06,250)])
        self.assertAlmostEqual(vad.start_threshold,.152)
        self.assertEqual(vad.stop_reason,"start_timeout")
        self.assertFalse(vad.accepted)

    def test_short_pauses_preserve_utterance(self):
        vad, events = self.run_fixture([(.005,25),(.06,20),(.005,15),(.06,20),(.005,40)])
        self.assertEqual(events.count("start"),1)
        self.assertTrue(vad.accepted)
        self.assertEqual(vad.stop_reason,"silence")

    def test_loud_transient_does_not_start_capture(self):
        vad, events = self.run_fixture([(.005,25),(1,1),(.005,250)])
        self.assertNotIn("start",events)
        self.assertEqual(vad.stop_reason,"start_timeout")

    def test_silence_is_bounded_and_fresh_cycle_recovers(self):
        vad, _ = self.run_fixture([(0,250)])
        self.assertEqual(vad.stop_reason,"start_timeout")
        recovered, _ = self.run_fixture([(.001,25),(.05,20),(0,40)])
        self.assertTrue(recovered.accepted)

    def test_alsa_gate_observes_left_channel_only(self):
        right_only = struct.pack("<ii",0,2**30)*100
        left_only = struct.pack("<ii",2**30,0)*100
        self.assertEqual(_normalized_alsa_left_rms(right_only),0)
        self.assertAlmostEqual(_normalized_alsa_left_rms(left_only),.5)

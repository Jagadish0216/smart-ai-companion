"""Synthetic signals only; supplied scalar RMS is not an audio recording."""
from django.test import SimpleTestCase
from assistant.voice.capture_vad import AdaptiveCaptureVAD
from assistant.voice.spectral_onset import SpectralOnset
from scripts import voice_loop as voice, validate_offline_voice as validation


def pcm(rms, speech=False, right=0):
    # Fan: low-frequency motor plus broadband harmonics. Speech is represented
    # by two formants, not claimed to be actual recorded human speech.
    import numpy as np
    t=np.arange(960)/48000
    signal=np.sin(2*np.pi*100*t)+.1*np.sin(2*np.pi*450*t)+.1*np.sin(2*np.pi*2600*t)
    if speech: signal+=.8*np.sin(2*np.pi*700*t)+.8*np.sin(2*np.pi*1800*t)
    signal*=rms/np.sqrt(np.mean(signal**2))
    stereo=np.column_stack((signal*2147483648, np.full(960,right)))
    return stereo.astype("<i4").tobytes()


class RelativeOnsetTests(SimpleTestCase):
    def calibrated(self):
        detector=SpectralOnset();vad=AdaptiveCaptureVAD()
        for _ in range(25):
            frame=pcm(.01288575)
            detector.observe_calibration(detector.features(frame))
            vad.feed(.01288575,.02)
        self.assertTrue(detector.freeze())
        vad.enable_relative_onset();vad.open_start_window()
        return vad,detector

    def feed(self,vad,detector,levels):
        events=[]
        for level,speech in levels:
            evidence=detector.evidence(detector.features(pcm(level,speech)))
            event=vad.feed(level,.02,speech_evidence=evidence)
            if event: events.append(event)
            if vad.stop_reason: break
        return events

    def test_measured_constraints_allow_corroborated_modest_speech_below_old_gate(self):
        vad,detector=self.calibrated()
        self.assertAlmostEqual(vad.start_threshold,.034214375)
        self.assertLess(vad.relative_start_threshold,.020136)
        self.assertGreater(vad.relative_start_threshold,vad.noise_floor)
        self.assertLess(vad.relative_continue_threshold,vad.relative_start_threshold)
        events=self.feed(vad,detector,[(.01288575,False)]*10+[(.020136,True)]*25+[(.01288575,False)]*40)
        self.assertIn("start",events)
        self.assertTrue(vad.accepted)
        self.assertEqual(vad.stop_reason,"silence")
        self.assertAlmostEqual(vad.speech_duration,.5)

    def test_same_waiting_max_is_not_speech_without_spectral_change(self):
        vad,detector=self.calibrated()
        self.feed(vad,detector,[(.020136,False)]*250)
        self.assertFalse(vad.accepted)
        self.assertEqual(vad.stop_reason,"start_timeout")
        self.assertAlmostEqual(vad.max_wait_rms,.020136)

    def test_stationary_fan_uniform_gain_and_isolated_peaks_do_not_trigger(self):
        for levels in ([(.01288575,False)]*250,
                       [(.04,False)]*250,
                       ([(.01288575,False)]*8+[(.020136,True)]*2)*25):
            vad,detector=self.calibrated()
            self.assertNotIn("start",self.feed(vad,detector,levels))
            self.assertFalse(vad.accepted)
            self.assertEqual(vad.stop_reason,"start_timeout")

    def test_randomized_broadband_fan_gain_changes_are_not_accepted(self):
        import numpy as np
        for seed in range(5):
            rng=np.random.default_rng(seed)
            def noise_frame(rms):
                t=np.arange(960)/48000
                signal=.6*np.sin(2*np.pi*100*t)+rng.normal(size=960)
                signal*=rms/np.sqrt(np.mean(signal**2))
                return np.column_stack((signal*2147483648,np.zeros(960))).astype("<i4").tobytes()
            vad=AdaptiveCaptureVAD();detector=SpectralOnset()
            for _ in range(25):
                detector.observe_calibration(detector.features(noise_frame(.01288575)),.01288575)
                vad.feed(.01288575,.02)
            self.assertTrue(detector.freeze(vad.noise_floor))
            vad.enable_relative_onset();vad.open_start_window()
            for _ in range(250):
                evidence=detector.evidence(detector.features(noise_frame(.020136)))
                vad.feed(.020136,.02,speech_evidence=evidence)
                if vad.stop_reason: break
            self.assertFalse(vad.accepted,seed)
            self.assertEqual(vad.stop_reason,"start_timeout")

    def test_onset_confirmation_short_burst_and_normal_pauses(self):
        vad,detector=self.calibrated()
        self.assertEqual(self.feed(vad,detector,[(.019,True)]*2),[])
        self.assertFalse(vad.speech_started)
        self.assertEqual(self.feed(vad,detector,[(.019,True)]),["start"])
        self.feed(vad,detector,[(.01288575,False)]*40)
        self.assertFalse(vad.accepted)
        self.assertFalse(vad.speech_started)
        self.feed(vad,detector,[(.019,True)]*25+[(.01288575,False)]*15+[(.019,True)]*25+[(.01288575,False)]*40)
        self.assertTrue(vad.accepted)
        self.assertEqual(vad.stop_reason,"silence")
        self.assertAlmostEqual(vad.speech_duration,1)

    def test_relative_hysteresis_and_maximum_duration_remain_bounded(self):
        vad,detector=self.calibrated()
        self.feed(vad,detector,[(.019,True)]*15)
        self.feed(vad,detector,[(.01288575,False)]*10+[(.0148,True)]*5)
        self.assertAlmostEqual(vad.silence_duration,0)
        self.assertAlmostEqual(vad.speech_duration,.4)
        self.feed(vad,detector,[(.01288575,False)]*40)
        self.assertEqual(vad.stop_reason,"silence")
        vad,detector=self.calibrated();vad.max_seconds=.4
        self.feed(vad,detector,[(.019,True)]*30)
        self.assertEqual(vad.stop_reason,"max_duration")
        self.assertAlmostEqual(vad.capture_duration,.4)

    def test_spectral_calibration_also_excludes_loud_transients(self):
        detector=SpectralOnset()
        for level in [.006]*12+[.04]*13:
            detector.observe_calibration(detector.features(pcm(level,speech=level>.02)),level)
        self.assertTrue(detector.freeze(background_rms=.006))
        self.assertFalse(detector.evidence(detector.features(pcm(.006))))
        self.assertTrue(detector.evidence(detector.features(pcm(.015,True))))

    def test_right_channel_is_ignored_and_partial_frames_never_supply_evidence(self):
        detector=SpectralOnset()
        import numpy as np
        np.testing.assert_array_equal(detector.features(pcm(.012,True,0)),detector.features(pcm(.012,True,1000000000)))
        self.assertIsNone(detector.features(b"partial"))
        self.assertFalse(detector.evidence(None))
        for _ in range(25): detector.observe_calibration(detector.features(b"\0"*7680))
        self.assertFalse(detector.freeze())

    def test_background_storage_is_bounded_frozen_and_legacy_gate_unchanged(self):
        detector=SpectralOnset()
        feature=detector.features(pcm(.01288575))
        for _ in range(1000): detector.observe_calibration(feature)
        self.assertEqual(len(detector.samples),128)
        self.assertTrue(detector.freeze())
        baseline=detector.baseline.copy()
        detector.observe_calibration(detector.features(pcm(.04,True)))
        self.assertEqual(len(detector.samples),0)
        self.assertTrue((detector.baseline==baseline).all())
        vad=AdaptiveCaptureVAD()
        for _ in range(25): vad.feed(.01288575,.02)
        vad.open_start_window()
        for _ in range(250): vad.feed(.020136,.02,speech_evidence=True)
        self.assertFalse(vad.accepted)
        self.assertEqual(vad.stop_reason,"start_timeout")


class NativeSpeechOnsetTests(SimpleTestCase):
    def test_native_corroborated_speech_preserves_preroll_and_readiness(self):
        from assistant.tests_capture_readiness import NativeCaptureReadinessTests
        import os,wave
        helper=NativeCaptureReadinessTests();case=helper.case();self.addCleanup(helper.doCleanups)
        trace=validation.Trace();ready=[]
        chunks=[pcm(.08)]*75+[pcm(.00940344)]*50+[pcm(.01288575)]*25
        chunks += [pcm(.01288575)]*10+[pcm(.020136,True)]*25+[pcm(.01288575)]*2
        case.process.stdout.read.side_effect=chunks
        read_fd,write_fd=os.pipe()
        try:
            with validation.observe_pipeline(voice,trace,"public",True,ready_fd=write_fd):
                voice._timed_call({},"record",voice.record_audio_with_capture_vad,case.output_path,
                    "hw:test",1,.02,.04,10,2,backend="alsa",ready_lifecycle=True,preparation_policy="settled_fan",
                    on_capture_ready=lambda:ready.append(case.process.stdout.read.call_count))
            self.assertEqual(ready,[150])
            self.assertIn(b"MICROPHONE READY",os.read(read_fd,200))
        finally: os.close(read_fd)
        summary=trace.data["capture_vad_summary"]
        self.assertTrue(summary["accepted"])
        self.assertTrue(summary["speech_onset"]["available"])
        self.assertLess(summary["relative_start_threshold"],.020136)
        self.assertGreater(summary["start_threshold"],.034)
        self.assertIn("endpoint_detected",trace.data["events_ms"])
        with wave.open(str(case.output_path),"rb") as wav:
            # First speech begins at frame 160; confirmation completes at 162,
            # retaining the unchanged 15-frame native pre-roll.
            self.assertEqual(wav.readframes(wav.getnframes()),b"".join(chunks[148:]))
        case.process.terminate.assert_called_once()

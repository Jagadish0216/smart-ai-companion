#!/usr/bin/env python3
"""Opt-in existing-public-audio analysis, production VAD replay and one guarded STT trial."""
from __future__ import annotations

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import compare_offline_stt as stt
from assistant.voice.capture_vad import AdaptiveCaptureVAD

VARIANTS = ("baseline", "normalize", "highpass")


class SingleVariant(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        if getattr(namespace,self.dest,None) is not None:
            parser.error("Select exactly one variant once")
        setattr(namespace,self.dest,value)


def load_left(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError("audio_exceeds_size_limit")
    with wave.open(str(path), "rb") as wav:
        rate, channels, width, count = wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()
        if wav.getcomptype() != "NONE" or (rate, channels, width) not in ((48000,2,4),(16000,1,2)):
            raise ValueError("native_stereo_s32_or_normalized_mono_pcm16_required")
        if not 0 < count <= 30*rate:
            raise ValueError("audio_exceeds_30_second_limit")
        pcm = wav.readframes(count)
        if len(pcm) != count*width*channels:
            raise ValueError("truncated_audio")
    values = array("i" if width == 4 else "h", pcm)
    if sys.byteorder != "little": values.byteswap()
    left = [v/(2**(8*width-1)) for v in values[::channels]]
    return left, dict(sample_rate=rate, channels=channels, sample_width_bytes=width,
                      duration_seconds=count/rate, analysis_channel="left" if channels==2 else "mono")


def rms(values):
    return math.sqrt(sum(v*v for v in values)/len(values)) if values else 0.0


def db(value):
    return 20*math.log10(value) if value > 0 else None


def quantile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered)-1)*fraction)]


def analyze(path):
    left, info = load_left(path)
    size = round(info["sample_rate"]*.02)
    frames = [left[i:i+size] for i in range(0,len(left),size)]
    levels = [rms(frame) for frame in frames]
    floor = quantile(levels,.1)
    active = [frame for frame,level in zip(frames,levels) if level > max(1e-6,floor*math.sqrt(10))]
    active_rms = math.sqrt(sum(sum(v*v for v in f) for f in active)/sum(map(len,active))) if active else None
    peak, overall = max(map(abs,left)), rms(left)
    return dict(**info, fingerprint=stt.fingerprint(path), peak=peak, rms=overall,
        rms_dbfs=db(overall), peak_dbfs=db(peak), peak_to_rms=peak/overall if overall else None,
        clipping_percent=100*sum(abs(v)>=1-1/(2**(8*info["sample_width_bytes"]-1)) for v in left)/len(left),
        frame_rms=dict(frame_seconds=.02,count=len(levels), **{f"p{p}":quantile(levels,p/100) for p in (10,50,90,99)}),
        estimated_background_floor=floor, noise_estimate_definition="lower_decile_frame_rms_proxy_not_confirmed_noise",
        estimated_active_rms=active_rms, estimated_active_dbfs=db(active_rms) if active_rms else None,
        active_estimate_definition="frames_over_lower_decile_by_10dB_not_verified_speech",
        signal_to_noise_db=None, snr_unavailable_reason="no_confirmed_noise_only_segment",
        noise_reduction=dict(status="skipped",reason="no_confirmed_stationary_noise_only_segment"))


def filter_chain(variant, peak, channels):
    if variant not in VARIANTS:
        raise ValueError("unsupported_variant_noise_reduction_not_justified")
    chain = ["pan=mono|c0=c0"] if channels==2 else []
    chain.append("aresample=16000")
    if variant == "normalize":
        # Peak-based, never boost silence-inclusive whole-file RMS to a speech target.
        gain = min(4.0,.5/peak) if peak else 1.0
        chain += [f"volume={gain:.9f}","alimiter=limit=0.95:level=false"]
    if variant == "highpass": chain += ["highpass=f=80:p=2","alimiter=limit=0.95:level=false"]
    return ",".join(chain)


def prepare_variant(source, directory, variant):
    measured = analyze(source)
    chain = filter_chain(variant,measured["peak"],measured["channels"])
    output = directory / (variant+".wav")
    if output.resolve() == source.resolve() or output.exists():
        raise ValueError("new_output_required")
    command = ["ffmpeg","-nostdin","-n","-i",str(source),"-af",chain,
               "-ar","16000","-ac","1","-c:a","pcm_s16le",str(output)]
    try:
        result = subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25,check=False)
        if result.returncode: raise ValueError("ffmpeg_processing_failed")
        stt.read_audio(output)  # Exact 16 kHz mono PCM16 validation.
        after = analyze(output)
        if variant != "baseline" and after["peak"] > .951:
            raise ValueError("normalized_peak_exceeds_limiter_bound")
        return output, dict(variant=variant,filter_chain=chain,source=measured,processed=after,
                            ffmpeg_timeout_seconds=25, noise_reduction="skipped_no_noise_reference")
    except BaseException:
        if output.is_file(): output.unlink()
        raise


def replay(path):
    # Settings import reads existing numeric configuration, without django.setup,
    # logging initialization, provider factories or database connections.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
    import config.settings as config
    options = dict(start_threshold=float(config.VOICE_CAPTURE_VAD_START_THRESHOLD),
        silence_seconds=float(config.VOICE_CAPTURE_VAD_SILENCE_SECONDS),
        max_seconds=float(config.VOICE_CAPTURE_VAD_MAX_SECONDS),
        start_timeout_seconds=float(config.VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS),
        adaptive=str(config.VOICE_CAPTURE_VAD_ADAPTIVE).lower() in ("1","true","yes","on"),
        noise_calibration_seconds=float(config.VOICE_CAPTURE_VAD_NOISE_CALIBRATION_SECONDS),
        start_confirm_ms=int(config.VOICE_CAPTURE_VAD_START_CONFIRM_MS),
        min_speech_ms=int(config.VOICE_CAPTURE_VAD_MIN_SPEECH_MS))
    vad = AdaptiveCaptureVAD(**options)
    left, info = load_left(path)
    chunk = round(info["sample_rate"]*.02)
    events = []
    for offset in range(0,len(left),chunk):
        frame = left[offset:offset+chunk]
        event = vad.feed(rms(frame),len(frame)/info["sample_rate"])
        if event: events.append(dict(event=event,at_audio_seconds=vad.elapsed))
        if vad.stop_reason: break
    return dict(simulated_replay=True, origin="first_file_frame_calibration_starts_at_zero",
        configured_capture_vad_enabled=config.VOICE_CAPTURE_VAD_ENABLED,
        configured_warmup_seconds=float(config.VOICE_INPUT_WARMUP_SECONDS),
        device_timing="warmup_pipe_buffering_and_wall_clock_not_simulated",options=options,
        events=events,speech_detected=any(e["event"]=="start" for e in events),accepted=vad.accepted,
        status="stopped" if vad.stop_reason else "incomplete_file_ended",
        **{key:getattr(vad,key) for key in ("noise_floor","start_threshold","continue_threshold",
           "max_wait_rms","speech_duration","capture_duration","elapsed","stop_reason")})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--run",action="store_true",help="One guarded Whisper inference, explicit variant required")
    mode.add_argument("--prepare",action="store_true",help="Prepare one variant only, no inference")
    parser.add_argument("--audio",type=Path,required=True)
    parser.add_argument("--operator-approved-public-audio",action="store_true")
    parser.add_argument("--variant",choices=VARIANTS,action=SingleVariant)
    parser.add_argument("--output-dir",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--whisper-bin",type=Path)
    parser.add_argument("--whisper-model",type=Path)
    parser.add_argument("--reference",default=stt.REFERENCE)
    from scripts.compare_offline_models import local_host
    parser.add_argument("--host",type=local_host,default="http://127.0.0.1:11434")
    args=parser.parse_args(argv)
    if not args.operator_approved_public_audio:
        parser.error("Explicit approval for this existing public recording is required")
    if (args.run or args.prepare) and not args.variant:
        parser.error("Select exactly one variant")
    if args.run:
        if not all((args.output,args.whisper_bin,args.whisper_model)) or args.output.exists():
            parser.error("Existing Whisper paths and a new output report are required")
        return stt.run(args,whisper_variant=args.variant)
    if args.prepare:
        if not args.output_dir or args.output_dir.exists(): parser.error("New output directory required")
        args.output_dir.mkdir(parents=False)
        try:
            _, report=prepare_variant(args.audio,args.output_dir,args.variant)
            (args.output_dir/"analysis.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        except Exception:
            print(json.dumps(dict(status="failed",reason="audio_processing_failed")))
            return 2
    else:
        analysis=analyze(args.audio)
        report=dict(status="analysis_only_no_inference",analysis=analysis,vad_replay=replay(args.audio),
                    planned_filters={v:filter_chain(v,analysis["peak"],analysis["channels"]) for v in VARIANTS})
    print(json.dumps(report,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

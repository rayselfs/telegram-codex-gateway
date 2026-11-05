"""Offline speech recognition worker, isolated from the gateway's dependencies."""
import json
from pathlib import Path
import sys


def transcribe(path):
    import av
    from faster_whisper import WhisperModel
    # Decode at 16 kHz with a hard duration cap before inference (untrusted media).
    import numpy as np
    frames = []
    count = 0
    with av.open(str(path)) as container:
        resampler = av.audio.resampler.AudioResampler(format='s16', layout='mono', rate=16000)
        for frame in container.decode(audio=0):
            for pcm in resampler.resample(frame):
                count += pcm.samples
                if count > 16000 * 300:
                    raise ValueError('Voice exceeds five minutes')
                frames.append(pcm.to_ndarray().reshape(-1))
        for pcm in resampler.resample(None):
            count += pcm.samples
            if count > 16000 * 300:
                raise ValueError('Voice exceeds five minutes')
            frames.append(pcm.to_ndarray().reshape(-1))
    if not frames:
        raise ValueError('No audio')
    audio = np.concatenate(frames).astype(np.float32) / 32768.0
    model = WhisperModel('D:/CodexTelegram/runtime/voice-model', device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
    segments, info = model.transcribe(audio, beam_size=5, vad_filter=True,
        initial_prompt='請使用繁體中文。人名：User A、User B。助理：Codex Gateway。')
    text = ''.join(s.text for s in segments).strip()
    if not text or len(text)>12000:
        raise ValueError('No reliable speech')
    return {'text':text, 'language':info.language}


if __name__ == '__main__':
    try:
        print(json.dumps(transcribe(Path(sys.argv[1])), ensure_ascii=False))
    except Exception:
        print(json.dumps({'error':'語音辨識未完成；請改傳文字或較短且清楚的語音。'}, ensure_ascii=False))
        sys.exit(1)

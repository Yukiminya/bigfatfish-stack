import math
import os
import threading
import wave
from contextlib import suppress
from pathlib import Path


class LocalVoice:
    """按需加载本地语音模型，并在多个合成请求间复用。"""

    def __init__(self, voice_dir: str, voice_name: str = "", speed: float = 1.0, threads: int = 2):
        self.voice_dir = Path(voice_dir)
        self.voice_name = voice_name
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("本地语音语速必须是大于零的有限数值")
        self.speed = speed
        self.threads = threads
        self._lock = threading.Lock()
        self._tts = None

    def _model_files(self) -> tuple[Path, Path]:
        candidates: list[Path] = []
        if self.voice_name:
            candidates.append(self.voice_dir / self.voice_name)
        else:
            # 先看根目录：雪糕鱼本来的声音（fanchen-C）就放在 /android-share/tts 根下
            candidates.append(self.voice_dir)
            candidates.extend(sorted(self.voice_dir.iterdir()))
        for folder in candidates:
            if folder.is_dir():
                onnx = sorted(path for path in folder.glob("*.onnx") if path.is_file())
                if onnx:
                    return folder, onnx[0]
        raise FileNotFoundError(f"找不到本地语音模型（{self.voice_dir / self.voice_name}）")

    def _build(self):
        import sherpa_onnx as sherpa

        folder, onnx = self._model_files()
        rules = ",".join(str(path) for path in sorted(folder.glob("*.fst")))
        voices = folder / "voices.bin"
        tokens = str(folder / "tokens.txt")
        if voices.is_file():
            lexicon = ",".join(str(path) for path in sorted(folder.glob("lexicon*.txt")))
            config = sherpa.OfflineTtsModelConfig(
                kokoro=sherpa.OfflineTtsKokoroModelConfig(
                    model=str(onnx), voices=str(voices), tokens=tokens, lexicon=lexicon,
                ),
                num_threads=self.threads,
            )
        else:
            config = sherpa.OfflineTtsModelConfig(
                vits=sherpa.OfflineTtsVitsModelConfig(
                    model=str(onnx), lexicon=str(folder / "lexicon.txt"), tokens=tokens,
                ),
                num_threads=self.threads,
            )
        return sherpa.OfflineTts(
            sherpa.OfflineTtsConfig(model=config, rule_fsts=rules, max_num_sentences=1)
        )

    def synth(self, text: str, wav_path: str | Path, sid: int = 0) -> None:
        import numpy as np

        with self._lock:
            if self._tts is None:
                self._tts = self._build()
            try:
                audio = self._tts.generate(text, sid=sid, speed=self.speed)
            except Exception:
                if sid == 0:
                    raise
                audio = self._tts.generate(text, sid=0, speed=self.speed)

            samples = np.asarray(audio.samples)
            sample_rate = audio.sample_rate
            if not samples.size or sample_rate <= 0:
                raise ValueError("本地语音模型返回了空音频或无效采样率")
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        path = Path(wav_path)
        tmp = path.with_suffix(".wav.tmp")
        try:
            with wave.open(str(tmp), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(sample_rate)
                out.writeframes(pcm.tobytes())
            os.replace(tmp, path)
        except OSError:
            with suppress(OSError):
                tmp.unlink()
            raise

"""Синтетическая библиотека эмбиента для хвоста.

Пишет оригинальные дорожки в assets/ambient/. Чужих записей нет, в ролик
их можно ставить без атрибуции. Полевые записи, если появятся, просто
кладутся в те же папки и заменяют эти файлы.

Пример:
    python scripts/make_ambient_library.py
    python scripts/make_ambient_library.py --minutes 40 --only rain
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parent.parent
AMBIENT = ROOT / "assets" / "ambient"
SR = 48_000
BLOCK = 15 * SR
TAIL = 4 * SR
CTRL_RATE = 40.0
TARGET_RMS = 0.085
TARGET_PEAK = 0.82


@dataclass(frozen=True)
class Preset:
    category: str
    filename: str
    kind: str
    seed: int


PRESETS: tuple[Preset, ...] = (
    Preset("rain", "rain_on_glass.mp3", "rain_glass", 1101),
    Preset("rain", "distant_night_rain.mp3", "rain_far", 1102),
    Preset("fireplace", "hearth_crackle.mp3", "fire", 2201),
    Preset("fireplace", "low_embers.mp3", "embers", 2202),
    Preset("night_wind", "open_gusts.mp3", "wind", 3301),
    Preset("night_wind", "leaves_in_wind.mp3", "leaves", 3302),
    Preset("water", "small_brook.mp3", "brook", 4401),
    Preset("water", "soft_shore.mp3", "shore", 4402),
    Preset("", "soft_room_air.mp3", "room", 5501),
)


class Sos:
    def __init__(self, kind: str, freq: float | tuple[float, float], order: int = 2) -> None:
        self.sos = butter(order, freq, btype=kind, fs=SR, output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)

    def __call__(self, block: np.ndarray) -> np.ndarray:
        out, self.zi = sosfilt(self.sos, block, zi=self.zi)
        return out


def ffmpeg_bin() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ensure_lame(ffmpeg: str) -> None:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if "libmp3lame" not in (proc.stdout or ""):
        raise SystemExit("В ffmpeg проекта нет libmp3lame, mp3 для хвоста не собрать.")


def probe_duration(ffmpeg: str, path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    import re

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def sample_curve(curve: np.ndarray, start: int, count: int) -> np.ndarray:
    pos = (start + np.arange(count, dtype=np.float64)) * (CTRL_RATE / SR)
    i0 = np.floor(pos).astype(np.int32)
    np.clip(i0, 0, curve.size - 2, out=i0)
    frac = pos - i0
    return (1.0 - frac) * curve[i0] + frac * curve[i0 + 1]


def swell_curve(
    duration: float,
    rng: np.random.Generator,
    *,
    floor: float,
    length: tuple[float, float],
    gap: tuple[float, float],
    power: float = 1.0,
) -> np.ndarray:
    n = int(duration * CTRL_RATE) + 3
    env = np.full(n, floor, dtype=np.float64)
    t = 0.0
    while t < duration:
        dur = float(rng.uniform(*length))
        amp = float(rng.uniform(0.62, 1.0))
        i0 = int(t * CTRL_RATE)
        i1 = min(n, int((t + dur) * CTRL_RATE))
        if i1 - i0 >= 2:
            u = np.linspace(0.0, 1.0, i1 - i0)
            rise = u / 0.32
            fall = 1.0 - (u - 0.32) / 0.68
            shape = np.where(u < 0.32, rise, fall)
            shape = np.clip(shape, 0.0, 1.0) ** power
            env[i0:i1] = np.maximum(env[i0:i1], floor + (amp - floor) * shape)
        t += dur + float(rng.uniform(*gap))
    return env


def _kernel(
    kind: str,
    decay: float,
    freq: float,
    amp: float,
    rng: np.random.Generator,
) -> np.ndarray:
    if kind == "whoosh":
        klen = max(8, int(decay * SR))
        u = np.linspace(0.0, 1.0, klen)
        env = np.sin(np.pi * u) ** 2
        wave = rng.standard_normal(klen) * env * amp
        alpha = 0.06
        acc = 0.0
        for i in range(klen):
            acc += alpha * (wave[i] - acc)
            wave[i] = acc
        return wave
    klen = max(8, int(decay * (10.0 if kind == "tick" else 8.0) * SR))
    t = np.arange(klen, dtype=np.float64) / SR
    env = np.exp(-t / decay)
    if kind == "tick":
        wave = np.sin(2.0 * np.pi * freq * t) * env
        wave[0] += 0.65
    else:
        wave = rng.standard_normal(klen) * env
    return wave * amp


def make_events(kind: str, duration: float, rng: np.random.Generator) -> list[tuple[int, str, float, float, float]]:
    specs: list[tuple[str, float]] = []
    if kind == "rain_glass":
        specs.append(("tick", 16.0))
    elif kind == "rain_far":
        specs.append(("tick", 2.2))
    elif kind == "fire":
        specs.append(("crackle", 6.5))
        specs.append(("whoosh", 0.12))
    elif kind == "embers":
        specs.append(("crackle", 1.3))
    elif kind == "room":
        specs.append(("tick", 0.045))
    events: list[tuple[int, np.ndarray]] = []
    for impulse, rate in specs:
        count = int(rng.poisson(duration * rate))
        if count <= 0:
            continue
        times = np.sort(rng.random(count) * duration)
        for moment in times:
            if impulse == "tick":
                if kind == "room":
                    decay = float(rng.uniform(0.02, 0.05))
                    freq = float(rng.uniform(140.0, 320.0))
                    amp = float(rng.uniform(0.03, 0.07))
                elif kind == "rain_far":
                    decay = float(rng.uniform(0.01, 0.02))
                    freq = float(rng.uniform(700.0, 1800.0))
                    amp = float(rng.uniform(0.02, 0.06))
                else:
                    decay = float(rng.uniform(0.004, 0.012))
                    freq = float(rng.uniform(1500.0, 5400.0))
                    amp = float(rng.uniform(0.05, 0.22) ** 1.15)
            elif impulse == "whoosh":
                decay = float(rng.uniform(0.28, 0.55))
                freq = 0.0
                amp = float(rng.uniform(0.04, 0.1))
            else:
                decay = float(rng.uniform(0.004, 0.016))
                freq = 0.0
                amp = float(rng.uniform(0.025, 0.12) ** 1.15)
                if rng.random() < 0.05:
                    amp *= 1.45
            events.append((int(float(moment) * SR), impulse, decay, freq, amp))
    events.sort(key=lambda item: item[0])
    return events


class Bed:
    """Фильтры одного файла. Состояние живёт между блоками, стыков не слышно."""

    def __init__(self, kind: str, duration: float, rng: np.random.Generator) -> None:
        self.kind = kind
        self.rng = rng
        self.duration = duration
        self.total = int(duration * SR)
        self.events = make_events(kind, duration, rng)
        self.cursor = 0
        self.carry = np.zeros(TAIL, dtype=np.float64)
        self.low = Sos("lowpass", 700.0)
        self.mid = Sos("bandpass", (500.0, 2400.0))
        self.high = Sos("bandpass", (2800.0, 7600.0))
        self.rumble = Sos("lowpass", 160.0)
        self.air = Sos("lowpass", 420.0)
        self.room_low = Sos("lowpass", 240.0)
        self.rustle = Sos("bandpass", (1800.0, 6200.0))
        self.flutter = Sos("lowpass", 14.0)
        self.brook = Sos("bandpass", (280.0, 1900.0))
        self.babble = Sos("lowpass", 7.0)
        self.shore = Sos("lowpass", 480.0)
        self.foam = Sos("bandpass", (2000.0, 7000.0))
        self.body = Sos("highpass", 45.0, order=1)
        self.env = self._envelope()

    def _envelope(self) -> np.ndarray:
        rng = self.rng
        if self.kind in {"rain_glass", "rain_far"}:
            return swell_curve(
                self.duration, rng, floor=0.78, length=(18.0, 42.0), gap=(0.0, 4.0), power=1.3
            )
        if self.kind == "wind":
            return swell_curve(
                self.duration, rng, floor=0.16, length=(4.0, 11.0), gap=(1.2, 5.5), power=0.85
            )
        if self.kind == "leaves":
            return swell_curve(
                self.duration, rng, floor=0.22, length=(3.5, 9.0), gap=(0.4, 3.0), power=0.9
            )
        if self.kind == "shore":
            return swell_curve(
                self.duration, rng, floor=0.04, length=(5.0, 9.5), gap=(2.5, 6.5), power=1.15
            )
        return np.ones(int(self.duration * CTRL_RATE) + 3, dtype=np.float64)

    def impulses(self, start: int, count: int) -> np.ndarray:
        audio = np.zeros(count + TAIL, dtype=np.float64)
        audio[:TAIL] += self.carry
        end = start + count
        while self.cursor < len(self.events) and self.events[self.cursor][0] < end:
            pos, impulse, decay, freq, amp = self.events[self.cursor]
            local = pos - start
            if 0 <= local < count:
                kernel = _kernel(impulse, decay, freq, amp, self.rng)
                stop = min(local + kernel.size, audio.size)
                audio[local:stop] += kernel[: stop - local]
            self.cursor += 1
        self.carry = audio[count : count + TAIL].copy()
        return audio[:count]

    def block(self, start: int, count: int) -> np.ndarray:
        rng = self.rng
        white = rng.standard_normal(count)
        env = sample_curve(self.env, start, count)
        kind = self.kind
        if kind in {"rain_glass", "rain_far"}:
            low = self.low(white)
            mid = self.mid(rng.standard_normal(count))
            high = self.high(rng.standard_normal(count))
            if kind == "rain_glass":
                bed = 0.42 * low + 0.34 * mid + 0.16 * high
            else:
                bed = 0.62 * low + 0.22 * mid + 0.04 * high
            wave = bed * env + self.impulses(start, count)
        elif kind in {"fire", "embers"}:
            rumble = self.rumble(white)
            air = self.air(rng.standard_normal(count))
            body = (0.7 if kind == "embers" else 0.48) * rumble
            body += 0.05 * air
            cracks = self.impulses(start, count)
            wave = body + cracks * (0.45 if kind == "embers" else 0.7)
        elif kind == "wind":
            wave = self.air(white) * env
        elif kind == "leaves":
            gust = self.air(white) * env * 0.65
            flutter = self.flutter(np.abs(rng.standard_normal(count)))
            rustle = self.rustle(rng.standard_normal(count)) * flutter * env
            wave = gust + 0.22 * rustle
        elif kind == "brook":
            carrier = self.brook(white)
            mod = self.babble(np.abs(rng.standard_normal(count)))
            wave = carrier * np.clip(mod * 1.2, 0.08, 3.0)
            wave += 0.28 * self.low(rng.standard_normal(count))
        elif kind == "shore":
            wash = self.shore(white) * env
            foam = self.foam(rng.standard_normal(count)) * np.clip(env, 0.0, 1.0) ** 1.6
            wave = wash + 0.12 * foam
        else:
            wave = 0.85 * self.room_low(white) + 0.08 * self.air(rng.standard_normal(count))
            wave = wave + self.impulses(start, count)
        wave = self.body(wave)
        fade = SR + SR // 2
        if start < fade:
            ramp = np.linspace(start / fade, (start + count) / fade, count, dtype=np.float64)
            wave *= np.clip(ramp, 0.0, 1.0)
        tail_from = self.total - fade
        if start + count > tail_from:
            local0 = max(0, tail_from - start)
            ramp = np.ones(count, dtype=np.float64)
            if local0 < count:
                remain = count - local0
                ramp[local0:] = np.linspace(1.0, 0.0, remain, endpoint=False)
            wave *= ramp
        return wave


def encode_mp3(ffmpeg: str, pcm: Path, dest: Path, gain: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".part.mp3")
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(SR),
            "-ac",
            "1",
            "-i",
            str(pcm),
            "-af",
            f"volume={gain:.6f}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(partial),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError((proc.stderr or "ffmpeg не собрал mp3")[-500:])
    partial.replace(dest)


def render_to(preset: Preset, seconds: float, dest: Path, ffmpeg: str) -> None:
    rng = np.random.default_rng(preset.seed)
    bed = Bed(preset.kind, seconds, rng)
    dest.parent.mkdir(parents=True, exist_ok=True)
    peak = 0.0
    sum_sq = 0.0
    written = 0
    started = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as handle:
        pcm_path = Path(handle.name)
    try:
        with pcm_path.open("wb") as handle:
            start = 0
            while start < bed.total:
                count = min(BLOCK, bed.total - start)
                block = bed.block(start, count)
                if not np.isfinite(block).all():
                    raise RuntimeError(f"{preset.filename}: нечисловые сэмплы")
                peak = max(peak, float(np.max(np.abs(block))))
                sum_sq += float(np.square(block).sum())
                handle.write(np.asarray(block, dtype=np.float32).tobytes())
                written += count
                start += count
                done_min = written / SR / 60.0
                if written == count or start >= bed.total or written % (SR * 60) < count:
                    elapsed = time.perf_counter() - started
                    print(
                        f"  {preset.filename}: {done_min:.0f}/{seconds / 60:.0f} мин, {elapsed:.0f} с",
                        flush=True,
                    )
        rms = float(np.sqrt(sum_sq / max(1, written)))
        gain = TARGET_RMS / max(rms, 1e-8)
        gain = min(gain, TARGET_PEAK / max(peak, 1e-8), 40.0)
        encode_mp3(ffmpeg, pcm_path, dest, gain)
    finally:
        pcm_path.unlink(missing_ok=True)


def selected(only: str) -> list[Preset]:
    text = only.strip().lower()
    if not text:
        return list(PRESETS)
    found = [
        preset
        for preset in PRESETS
        if text in preset.category.lower()
        or text in preset.filename.lower()
        or text in preset.kind.lower()
    ]
    if not found:
        names = ", ".join(preset.filename for preset in PRESETS)
        raise SystemExit(f"Нет пресета «{only}». Есть: {names}")
    return found


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def destination(preset: Preset, root: Path) -> Path:
    if preset.category:
        return root / preset.category / preset.filename
    return root / preset.filename


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать синтетический эмбиент для хвоста.")
    parser.add_argument("--minutes", type=float, default=40.0, help="Длина каждой дорожки.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Короткий прогон в временную папку.")
    parser.add_argument("--only", default="", help="Часть имени, категория или вид: rain, fire, brook.")
    parser.add_argument("--force", action="store_true", help="Перезаписать уже готовые файлы.")
    args = parser.parse_args()
    if args.minutes <= 0 and args.seconds <= 0:
        raise SystemExit("Нужна положительная длина.")
    ffmpeg = ffmpeg_bin()
    ensure_lame(ffmpeg)
    presets = selected(args.only)
    if args.seconds > 0:
        root = Path(tempfile.mkdtemp(prefix="ambient_preview_"))
        seconds = args.seconds
        skip_ready = False
    else:
        root = AMBIENT
        seconds = args.minutes * 60.0
        skip_ready = not args.force
    print(f"Дорожек: {len(presets)}, длина {seconds / 60:.1f} мин. Папка: {root}", flush=True)
    for preset in presets:
        dest = destination(preset, root)
        if skip_ready and dest.is_file():
            have = probe_duration(ffmpeg, dest)
            if have >= seconds - 3:
                print(f"Уже есть {_shown(dest)} ({have / 60:.1f} мин), пропуск.", flush=True)
                continue
        print(f"Собираю {_shown(dest)}", flush=True)
        render_to(preset, seconds, dest, ffmpeg)
        have = probe_duration(ffmpeg, dest)
        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"Готово {dest.name}: {have / 60:.1f} мин, {size_mb:.1f} МБ", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

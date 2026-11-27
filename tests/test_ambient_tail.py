"""План сегментов эмбиент-хвоста и короткий ffmpeg-прогон."""

from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ambient_tail import (  # noqa: E402
    AmbientTailSettings,
    brightness_at,
    collect_tracks,
    describe_tail,
    dim_start_for,
    plan_segments,
    probe_duration,
    render_segments,
    tail_seconds,
    timeline_length,
)
from isekai_video import ken_burns_vf  # noqa: E402


def test_plan_is_seeded_and_covers_target() -> None:
    tracks = [
        (Path("a.mp3"), 40 * 60),
        (Path("b.mp3"), 50 * 60),
        (Path("c.mp3"), 30 * 60),
    ]
    first = plan_segments(tracks, 3 * 3600, random.Random(7))
    second = plan_segments(tracks, 3 * 3600, random.Random(7))
    assert timeline_length(first) >= 3 * 3600 - 1
    assert first[0].xfade_sec == 0
    assert all(4 <= seg.xfade_sec <= 6 for seg in first[1:])
    assert all(abs(seg.gain_db) <= 1.5 for seg in first)
    signature = [
        (seg.path.name, round(seg.start_sec, 3), round(seg.duration_sec, 3), round(seg.gain_db, 3))
        for seg in first
    ]
    again = [
        (seg.path.name, round(seg.start_sec, 3), round(seg.duration_sec, 3), round(seg.gain_db, 3))
        for seg in second
    ]
    assert signature == again
    other = plan_segments(tracks, 3 * 3600, random.Random(8))
    other_sig = [(seg.path.name, round(seg.start_sec, 3)) for seg in other]
    assert other_sig != [(seg.path.name, round(seg.start_sec, 3)) for seg in first]


def test_short_files_still_cover_target() -> None:
    tracks = [(Path("short.mp3"), 40.0)]
    segments = plan_segments(tracks, 200, random.Random(2))
    assert all(seg.duration_sec <= 40.01 for seg in segments)
    assert timeline_length(segments) >= 199


def test_tail_and_dim() -> None:
    on = AmbientTailSettings(True, 600 * 60, "mix", 0.12, 12)
    off = AmbientTailSettings(False, 600 * 60, "mix", 0.12, 12)
    assert tail_seconds(20 * 60, on) == 580 * 60
    assert tail_seconds(700 * 60, on) == 0
    assert tail_seconds(20 * 60, off) == 0
    assert dim_start_for(15 * 60) == 15 * 60
    assert dim_start_for(90 * 60) == 37.5 * 60
    assert dim_start_for(90 * 60, dim_during_speech=False) == 90 * 60
    assert brightness_at(0, 100) == 1
    assert brightness_at(100, 100) == 1
    assert abs(brightness_at(100 + 90, 100) - 0.12) < 1e-6
    mid = brightness_at(100 + 45, 100)
    assert 0.12 < mid < 1


def test_describe_and_categories(tmp: Path) -> None:
    (tmp / "rain").mkdir()
    (tmp / "rain" / "drops.mp3").write_bytes(b"not-audio")
    (tmp / "soft.mp3").write_bytes(b"x")
    assert collect_tracks("rain", tmp) == [tmp / "rain" / "drops.mp3"]
    mixed = collect_tracks("mix", tmp)
    assert len(mixed) == 2
    text = describe_tail(18 * 60, enabled=True, target_minutes=600)
    assert "18.0 мин" in text
    assert "582.0 мин" in text
    assert "Хвост не добавляется" in describe_tail(18 * 60, enabled=False, target_minutes=600)


def test_fps_steps_down_to_end() -> None:
    from episode_timing import describe_fps_schedule, fps_at

    total = 180 * 60
    assert fps_at(0, total, 24, 2, 30) == 24
    assert fps_at(150 * 60, total, 24, 2, 30) == 2
    assert fps_at(0, total, 24, 24, 30) == 24
    text = describe_fps_schedule(total, 24, 2, 30)
    assert "24 fps" in text
    assert "2 fps" in text


def test_ken_burns_variant_zero_matches_old_zoom() -> None:
    vf = ken_burns_vf(0)
    assert "zoompan=z='1+0.0700*on/287'" in vf
    assert "s=1280x720" in vf
    assert ken_burns_vf(1) != vf
    assert "1.0700-0.0700*on/287" in ken_burns_vf(1)


def test_ffmpeg_crossfade_smoke() -> None:
    from ambient_tail import AmbientSegment, _run

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        tones = []
        for name, freq in (("a.wav", 220), ("b.wav", 330)):
            dest = root / name
            proc = _run(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={freq}:duration=8",
                    "-c:a",
                    "pcm_s16le",
                    str(dest),
                ]
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr)
            tones.append(dest)
        segments = [
            AmbientSegment(tones[0], 0, 6, 0.8, -0.4, 0),
            AmbientSegment(tones[1], 1, 6, -0.7, 0.5, 2),
        ]
        output = root / "tail.m4a"
        render_segments(segments, output, 9)
        got = probe_duration(output)
        assert output.stat().st_size > 1000
        assert 8.2 <= got <= 9.8, got


if __name__ == "__main__":
    test_plan_is_seeded_and_covers_target()
    test_short_files_still_cover_target()
    test_tail_and_dim()
    test_fps_steps_down_to_end()
    with tempfile.TemporaryDirectory() as raw:
        test_describe_and_categories(Path(raw))
    test_ken_burns_variant_zero_matches_old_zoom()
    test_ffmpeg_crossfade_smoke()
    print("ambient tail tests ok")

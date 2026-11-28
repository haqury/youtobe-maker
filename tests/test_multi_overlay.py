"""Тесты множественных оверлеев, случайного наложения и FFmpeg encode_batch."""

from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from animation_settings import (  # noqa: E402
    OVERLAYS_DIR,
    list_overlay_files,
    load_episode_animation,
    normalize_animation_settings,
    resolve_overlay_path,
    resolve_overlay_paths,
    save_episode_animation,
)
from episode_review import load_review, save_review  # noqa: E402
from isekai_video import (  # noqa: E402
    OverlayLayerSpec,
    _run,
    build_overlay_variants,
    encode_batch,
    pick_batch_overlay_variant,
    probe_duration,
)


def test_normalize_backward_compat_and_multi() -> None:
    legacy_single = normalize_animation_settings({"overlay_file": "rain_loop.mp4"})
    assert legacy_single["overlay_files"] == ["rain_loop.mp4"]
    assert legacy_single["overlay_file"] == "rain_loop.mp4"

    legacy_none = normalize_animation_settings({"overlay_file": "none"})
    assert legacy_none["overlay_files"] == []
    assert legacy_none["overlay_file"] == "none"

    legacy_auto = normalize_animation_settings({"overlay_file": "auto"})
    assert legacy_auto["overlay_files"] == ["auto"]

    multi = normalize_animation_settings(
        {
            "overlay_files": ["rain_loop.mp4", "ash_embers_loop.mp4", "rain_loop.mp4"],
            "overlay_random_mix": True,
            "overlay_max_layers": 3,
            "overlay_variants": 10,
        }
    )
    assert multi["overlay_files"] == ["rain_loop.mp4", "ash_embers_loop.mp4"]
    assert multi["overlay_file"] == "rain_loop.mp4"
    assert multi["overlay_random_mix"] is True
    assert multi["overlay_max_layers"] == 3
    assert multi["overlay_variants"] == 10

    cleared = normalize_animation_settings(
        {"overlay_files": [], "overlay_file": "rain_loop.mp4"}
    )
    assert cleared["overlay_files"] == []
    assert cleared["overlay_file"] == "none"
    assert resolve_overlay_paths(cleared) == []


def test_review_roundtrip_preserves_multi_overlays() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ep = Path(tmp) / "EP01_test"
        ep.mkdir()
        save_episode_animation(
            ep,
            {
                "animation_mode": "ffmpeg_25d",
                "overlay_files": ["rain_loop.mp4", "ash_embers_loop.mp4", "falling_stars_loop.mp4"],
                "overlay_opacity": 0.24,
                "overlay_random_mix": True,
                "overlay_max_layers": 3,
                "overlay_variants": 16,
            },
        )
        # Повторное save_review (например, пометка кадра) не должно стирать animation.
        review = load_review(ep)
        review["problem_images"] = [{"name": "01.jpg", "note": "test"}]
        save_review(ep, review)

        loaded = load_episode_animation(ep)
        assert loaded["overlay_files"] == [
            "rain_loop.mp4",
            "ash_embers_loop.mp4",
            "falling_stars_loop.mp4",
        ]
        assert loaded["overlay_random_mix"] is True
        assert loaded["overlay_max_layers"] == 3
        assert loaded["overlay_variants"] == 16
        assert abs(loaded["overlay_opacity"] - 0.24) < 1e-6


def test_resolve_real_overlays() -> None:
    disk_files = list_overlay_files()
    assert len(disk_files) >= 3
    auto_paths = resolve_overlay_paths({"overlay_files": ["auto"]})
    assert [p.name for p in auto_paths] == disk_files
    assert resolve_overlay_path({"overlay_files": ["auto"]}) == OVERLAYS_DIR / disk_files[0]


def test_random_stacking_and_schedule_diversity() -> None:
    paths = [
        OVERLAYS_DIR / "rain_loop.mp4",
        OVERLAYS_DIR / "ash_embers_loop.mp4",
        OVERLAYS_DIR / "falling_stars_loop.mp4",
    ]
    durations = {p: 10.0 for p in paths}
    variants_a = build_overlay_variants(
        paths,
        durations,
        base_opacity=0.22,
        random_mix=True,
        max_layers=3,
        variant_count=12,
        rng=random.Random(42),
    )
    variants_b = build_overlay_variants(
        paths,
        durations,
        base_opacity=0.22,
        random_mix=True,
        max_layers=3,
        variant_count=12,
        rng=random.Random(42),
    )
    assert variants_a == variants_b
    assert len(variants_a) == 12
    assert any(len(v) >= 2 for v in variants_a), "Ожидалось хотя бы одно наложение нескольких слоёв"
    assert any(layer.hflip for v in variants_a for layer in v)
    assert any(layer.start_sec > 0 for v in variants_a for layer in v)

    schedule = pick_batch_overlay_variant(60, 12, random.Random(99))
    assert len(schedule) == 60
    assert set(schedule) == set(range(12))
    assert all(schedule[i] != schedule[i + 1] for i in range(len(schedule) - 1))


def test_encode_batch_multi_overlay_ffmpeg_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clip = root / "clip.mp4"
        proc = _run(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=0x162238:s=1280x720:r=24:d=4",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-600:])
        specs = (
            OverlayLayerSpec(OVERLAYS_DIR / "rain_loop.mp4", 0.22, start_sec=1.5, hflip=True),
            OverlayLayerSpec(OVERLAYS_DIR / "ash_embers_loop.mp4", 0.19, start_sec=3.0, hflip=False),
            OverlayLayerSpec(OVERLAYS_DIR / "falling_stars_loop.mp4", 0.17, start_sec=2.2, hflip=True),
        )
        out = root / "batch_multi.mp4"
        encode_batch([clip], out, brightness=0.35, fps=24, overlays=specs)
        assert out.is_file() and out.stat().st_size > 1000
        dur = probe_duration(out)
        assert 3.5 <= dur <= 4.5, dur


if __name__ == "__main__":
    test_normalize_backward_compat_and_multi()
    test_review_roundtrip_preserves_multi_overlays()
    test_resolve_real_overlays()
    test_random_stacking_and_schedule_diversity()
    test_encode_batch_multi_overlay_ffmpeg_smoke()
    print("multi overlay tests ok")

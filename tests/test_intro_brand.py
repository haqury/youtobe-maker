"""Наследование интро и короткая вклейка через ffmpeg."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from channel_settings import (  # noqa: E402
    apply_intro_to_series,
    resolve_episode_branding,
    save_channel_settings,
)
from intro_brand import (  # noqa: E402
    _run,
    attach_intro,
    episode_title,
    prepare_intro_clip,
    probe_duration,
    text_alpha_expr,
)


def _write_channel(root: Path, use_intro: bool, text: str) -> None:
    channel = root / "channels" / "Demo"
    channel.mkdir(parents=True)
    (channel / "channel_config.md").write_text(
        "Channel Name: LumaDream: Learn Spanish\n",
        encoding="utf-8",
    )
    save_channel_settings(
        channel,
        {
            "branding": {
                "use_intro": use_intro,
                "intro_text": text,
                "crossfade_sec": 0.8,
            }
        },
    )


def test_series_inherits_channel_until_override() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_channel(root, True, "Night Channel")
        series = root / "channels" / "Demo" / "playlists" / "S01_rome"
        episode = series / "episodes" / "EP01_rain"
        episode.mkdir(parents=True)
        save_channel_settings(series, {"branding": {"use_intro": False, "intro_text": "Series"}})
        branding = resolve_episode_branding(episode)
        assert branding["use_intro"] is True
        assert branding["intro_text"] == "Night Channel"
        assert branding["crossfade_sec"] == 0.8

        raw = json.loads((series / "channel_settings.json").read_text(encoding="utf-8"))
        raw["branding"]["intro_override"] = True
        raw["branding"]["use_intro"] = False
        raw["branding"]["intro_text"] = "Only series"
        (series / "channel_settings.json").write_text(json.dumps(raw), encoding="utf-8")
        custom = resolve_episode_branding(episode)
        assert custom["use_intro"] is False
        assert custom["intro_text"] == "Only series"


def test_apply_writes_every_series() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_channel(root, True, "Shared")
        channel = root / "channels" / "Demo"
        for name in ("S01_a", "S02_b"):
            (channel / "playlists" / name).mkdir(parents=True)
        count = apply_intro_to_series(channel, {"use_intro": True, "intro_text": "Shared", "crossfade_sec": 0.8})
        assert count == 2
        saved = json.loads(
            (channel / "playlists" / "S02_b" / "channel_settings.json").read_text(encoding="utf-8")
        )
        assert saved["branding"]["use_intro"] is True
        assert saved["branding"]["intro_text"] == "Shared"
        assert saved["branding"]["intro_override"] is False


def test_episode_title_and_playlist_style() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        channel = root / "channels" / "Demo"
        channel.mkdir(parents=True)
        save_channel_settings(
            channel,
            {
                "branding": {
                    "use_intro": True,
                    "intro_text": "Night Channel",
                    "intro_text_style": "light_sans",
                }
            },
        )
        series = channel / "playlists" / "S01_rome"
        episode = series / "episodes" / "EP01_rain"
        episode.mkdir(parents=True)
        (episode / "metadata.md").write_text(
            "# Episode metadata\n## English Metadata\n### Titles\n1. Primary YouTube title\n1. Navigator's Cabin\n",
            encoding="utf-8",
        )
        assert episode_title(episode) == "Navigator's Cabin"
        save_channel_settings(
            series,
            {"branding": {"use_intro": False, "intro_text_style": "small_italic"}},
        )
        branding = resolve_episode_branding(episode)
        assert branding["use_intro"] is True
        assert branding["intro_text"] == "Night Channel"
        assert branding["intro_text_style"] == "small_italic"


def test_text_alpha_timings() -> None:
    expr = text_alpha_expr(1.2, 2.6, 1.0)
    assert expr.startswith("0.800*")
    for mark in ("1.200", "2.200", "3.800", "4.800"):
        assert mark in expr


def test_prepare_cache_and_copy_concat() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = folder / "brand_assets" / "intro.mp4"
        source.parent.mkdir()
        main = folder / "main.mp4"
        _require_ok(
            [
                "-f", "lavfi", "-i", "color=c=0x1a1a1a:s=320x180:d=3",
                "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(source),
            ]
        )
        _require_ok(
            [
                "-f", "lavfi", "-i", "color=c=0x334455:s=320x180:d=8",
                "-r", "24", "-force_key_frames", "3.500",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(main),
            ]
        )
        branding = {
            "intro_text_start": 0.4,
            "intro_text_duration": 1.2,
            "intro_fade_sec": 0.3,
            "intro_text_y": 0,
            "intro_text_style": "light_sans",
        }
        episode = folder / "episode"
        episode.mkdir()
        (episode / "metadata.md").write_text(
            "## English Metadata\n### Titles\n1. Navigator's Cabin\n2. Alternative title\n",
            encoding="utf-8",
        )
        prepared = prepare_intro_clip(source, branding, 320, 180, 24, "LumaDream", episode)
        again = prepare_intro_clip(source, branding, 320, 180, 24, "LumaDream", episode)
        assert again == prepared
        assert prepared.name.startswith("intro_")
        assert prepared.parent.name == "cache"
        merged, lead = attach_intro(main, prepared, 0.5, 24, folder / "work")
        intro_dur = probe_duration(prepared)
        assert abs(lead - (intro_dur - min(0.5, intro_dur * 0.45))) < 0.05
        expected = probe_duration(main) + lead
        assert abs(probe_duration(merged) - expected) < 0.75


def _require_ok(args: list[str]) -> None:
    proc = _run(args)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-800:])


if __name__ == "__main__":
    test_series_inherits_channel_until_override()
    test_apply_writes_every_series()
    test_episode_title_and_playlist_style()
    test_text_alpha_timings()
    test_prepare_cache_and_copy_concat()
    print("intro brand tests ok")

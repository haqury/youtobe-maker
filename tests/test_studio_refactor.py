"""Регрессионные тесты рефакторинга Studio (вложенные expander, _trim_media, dim_during_speech, tail_video guard)."""

from __future__ import annotations

import ast
import contextlib
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts", ROOT / "studio"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _ensure_optional_pkg_stubs() -> None:
    for pkg in ("pandas", "dotenv", "requests", "PIL"):
        if pkg in sys.modules:
            continue
        try:
            __import__(pkg)
        except ImportError:
            stub = types.ModuleType(pkg)
            if pkg == "pandas":
                class _DataFrame:
                    def __init__(self, *args, **kwargs):
                        self.empty = True
                        self.columns = []
                stub.DataFrame = _DataFrame
            elif pkg == "dotenv":
                stub.load_dotenv = lambda *a, **kw: None
            elif pkg == "PIL":
                stub.Image = types.SimpleNamespace(open=lambda *a, **k: None)
                sys.modules["PIL.Image"] = stub.Image
            sys.modules[pkg] = stub


_ensure_optional_pkg_stubs()


def test_no_nested_expanders_ast() -> None:
    """В studio/*.py ни один st.expander не должен быть лексически вложен в другой st.expander."""
    for py_path in sorted((ROOT / "studio").glob("*.py")):
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))

        def walk(node: ast.AST, depth: int) -> None:
            for child in ast.iter_child_nodes(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "st"
                    and child.func.attr == "expander"
                ):
                    assert depth == 0, f"Вложенный st.expander в {py_path.name}:{child.lineno}"
                    walk(child, depth + 1)
                elif isinstance(child, ast.With) and any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and isinstance(item.context_expr.func.value, ast.Name)
                    and item.context_expr.func.value.id == "st"
                    and item.context_expr.func.attr == "expander"
                    for item in child.items
                ):
                    assert depth == 0, f"Вложенный with st.expander в {py_path.name}:{child.lineno}"
                    for body_node in child.body:
                        walk(body_node, depth + 1)
                else:
                    walk(child, depth)

        walk(tree, 0)


def test_trim_media_ffmpeg_smoke() -> None:
    from isekai_video import _run, _trim_media, probe_duration

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src_vid = root / "src.mp4"
        proc = _run(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=0x102030:s=320x180:r=24:d=4",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(src_vid),
            ]
        )
        assert proc.returncode == 0, proc.stderr
        trimmed_vid = root / "trimmed.mp4"
        _trim_media(src_vid, trimmed_vid, start=0.0, duration=2.0)
        dur = probe_duration(trimmed_vid)
        assert 1.5 <= dur <= 2.5, dur

        src_aud = root / "src.mp3"
        proc_a = _run(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                "4.0",
                "-c:a",
                "libmp3lame",
                str(src_aud),
            ]
        )
        assert proc_a.returncode == 0, proc_a.stderr
        trimmed_aud = root / "trimmed.mp3"
        _trim_media(src_aud, trimmed_aud, start=1.0, duration=1.5)
        aud_dur = probe_duration(trimmed_aud)
        assert 1.0 <= aud_dur <= 2.0, aud_dur


def test_dim_during_speech_propagation() -> None:
    from ambient_tail import dim_start_for, settings_from_episode
    from episode_timing import episode_timing_from_config

    with tempfile.TemporaryDirectory() as tmp:
        ch = Path(tmp) / "channels" / "test_ch"
        ep = ch / "playlists" / "S01_test" / "episodes" / "S01E01_test"
        ep.mkdir(parents=True)
        (ch / "channel_config.md").write_text("Channel Name: Test\n", encoding="utf-8")
        (ep / "episode_config.json").write_text(
            json.dumps(
                {
                    "ambient_tail_enabled": True,
                    "target_total_minutes": 180.0,
                    "dim_during_speech": False,
                }
            ),
            encoding="utf-8",
        )
        timing = episode_timing_from_config(ep)
        assert timing["dim_during_speech"] is False
        settings = settings_from_episode(ep)
        assert settings.dim_during_speech is False
        assert dim_start_for(3000.0, dim_during_speech=settings.dim_during_speech) == 3000.0
        assert dim_start_for(3000.0, dim_during_speech=True) == 37.5 * 60


def test_mux_stage_requires_tail_video_when_tail_enabled() -> None:
    from isekai_video import LongBuildPlan, build_long_mux_stage
    from ambient_tail import AmbientTailSettings
    from long_build_stages import AMBIENT_BED, AMBIENT_TAIL, mark_ambient_stage, mark_video_stage

    with tempfile.TemporaryDirectory() as tmp:
        ep = Path(tmp) / "EP01_test"
        cache = ep / ".video_cache"
        cache.mkdir(parents=True)
        voice = ep / "voiceover.mp3"
        voice.write_bytes(b"fake")
        # Несуществующий/короткий video_all.mp4 (нельзя подменить duration без реального mp4)
        from isekai_video import _run

        vid = cache / "video_all.mp4"
        proc = _run(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=0x102030:s=320x180:r=24:d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(vid),
            ]
        )
        assert proc.returncode == 0
        mark_video_stage(ep, vid)
        (cache / AMBIENT_BED).write_bytes(b"x" * 512)
        (cache / AMBIENT_TAIL).write_bytes(b"x" * 512)
        mark_ambient_stage(ep)

        plan = LongBuildPlan(
            episode_dir=ep,
            voice=voice,
            duration=120.0,
            tail=AmbientTailSettings(
                enabled=True,
                target_total_sec=3600.0,
                category="mix",
                volume=0.12,
                voice_fade_out_sec=12.0,
            ),
            use_tail=True,
            total=3600.0,
        )
        try:
            build_long_mux_stage(plan)
        except RuntimeError as exc:
            assert "tail_video" in str(exc)
        else:
            raise AssertionError("Ожидалась ошибка при отсутствии tail_video")


def _install_mock_streamlit() -> list[int]:
    """Фейковый streamlit, который падает при вложенном st.expander."""
    depth_box = [0]
    mod = types.ModuleType("streamlit")
    mod.session_state = {}

    class _Box:
        def __init__(self, is_exp: bool = False) -> None:
            self.is_exp = is_exp

        def __enter__(self):
            if self.is_exp:
                if depth_box[0] > 0:
                    raise RuntimeError("StreamlitAPIException: Expanders may not be nested")
                depth_box[0] += 1
            return self

        def __exit__(self, *args):
            if self.is_exp:
                depth_box[0] -= 1

        def __getattr__(self, name):
            return getattr(mod, name)

    def _noop(*args, **kwargs):
        return None

    def _checkbox(*args, **kwargs):
        return True

    def _columns(spec, **kwargs):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Box(False) for _ in range(n)]

    mod.expander = lambda *a, **kw: _Box(True)
    mod.container = lambda *a, **kw: _Box(False)
    mod.popover = lambda *a, **kw: _Box(False)
    mod.form = lambda *a, **kw: _Box(False)
    mod.tabs = lambda labels: [_Box(False) for _ in labels]
    mod.columns = _columns
    mod.checkbox = _checkbox
    mod.toggle = _checkbox
    mod.button = lambda *a, **kw: False
    mod.form_submit_button = lambda *a, **kw: False
    mod.selectbox = lambda label, opts, index=0, **kw: opts[index] if opts else None
    mod.multiselect = lambda label, opts, default=None, **kw: list(default or [])
    mod.radio = lambda label, opts, **kw: opts[0] if opts else None
    mod.segmented_control = lambda label, options, **kw: options[0] if options else None
    mod.slider = lambda label, min_value=0, max_value=1, value=0, **kw: value
    mod.number_input = lambda label, value=0, **kw: value
    mod.text_input = lambda label, value="", **kw: value
    mod.text_area = lambda label, value="", **kw: value
    mod.markdown = _noop
    mod.caption = _noop
    mod.subheader = _noop
    mod.header = _noop
    mod.info = _noop
    mod.warning = _noop
    mod.error = _noop
    mod.success = _noop
    mod.toast = _noop
    mod.write = _noop
    mod.code = _noop
    mod.dataframe = _noop
    mod.metric = _noop
    mod.video = _noop
    mod.audio = _noop
    mod.image = _noop
    mod.link_button = _noop
    mod.sidebar = _Box(False)
    sys.modules["streamlit"] = mod
    return depth_box


def test_studio_blocks_no_nested_expanders_runtime() -> None:
    _install_mock_streamlit()
    import streamlit as st
    from studio.story_build_controls import render_story_build_block
    from studio.tail_build_controls import render_tail_build_block
    from studio.final_mux_controls import render_final_mux_block
    from studio.isekai_panel import render_isekai_production

    with tempfile.TemporaryDirectory() as tmp:
        ch = Path(tmp) / "channels" / "test_ch"
        ep = ch / "episodes" / "EP01_test"
        ep.mkdir(parents=True)
        (ch / "channel_config.md").write_text(
            "Channel Name: Test\nTARGET_LANG: Spanish\nNATIVE_LANG: English\n"
            "MENTOR_NATIVE: en-US-AndrewMultilingualNeural\n"
            "MENTOR_TARGET: es-ES-AlvaroNeural\n"
            "NPC_TARGET: es-ES-ElviraNeural\n",
            encoding="utf-8",
        )
        sample = ROOT / "templates" / "template_isekai_sleep" / "episodes" / "EP01_example" / "script.md"
        (ep / "script.md").write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

        def dummy_anim(episode_dir, ep_key, stems, *, channel_slug, channel_dir, use_expander=True):
            box = st.expander("anim") if use_expander else st.container(border=True)
            with box:
                st.caption("ok")

        with st.expander("1 — Сценарий"):
            render_isekai_production(
                ep,
                "test_ch",
                "EP01_test",
                editor_key=lambda p, s: f"ed_{p.name}",
                save_texts=lambda s, f: 0,
                launch_job=lambda *a, **k: None,
                acknowledge=lambda *a, **k: None,
                sync_editors=lambda *a, **k: None,
                show_section_headings=False,
                blocks=("script",),
            )
        with st.expander("4 — Сюжет"):
            render_story_build_block(
                ep,
                "EP01_test",
                launch_job=lambda *a, **k: None,
                persist_config=lambda *a, **k: None,
                channel_slug="test_ch",
                channel_dir=ch,
                render_animation=dummy_anim,
                frame_stem_options=["01", "02"],
            )
        with st.expander("5 — Хвост"):
            render_tail_build_block(
                ep,
                "EP01_test",
                launch_job=lambda *a, **k: None,
                channel_slug="test_ch",
            )
        with st.expander("6 — Финал"):
            render_final_mux_block(
                ep,
                "EP01_test",
                launch_job=lambda *a, **k: None,
                persist_config=lambda *a, **k: None,
                channel_slug="test_ch",
                channel_dir=ch,
            )


    from studio.episode_launch_ui import render_episode_launch_controls

    with tempfile.TemporaryDirectory() as tmp_ep:
        ch_root = Path(tmp_ep) / "channels" / "test_ch"
        ep_root = ch_root / "episodes"
        ep_root.mkdir(parents=True)
        (ch_root / "channel_config.md").write_text("Channel Name: Test\n", encoding="utf-8")
        st.session_state["studio_playlist_test_ch"] = "standalone"
        # Эмулируем нажатие "Создать папку" с пустым названием — не должно падать с UnboundLocalError
        orig_button = st.button
        st.button = lambda label, **kw: label == "Создать папку"
        try:
            render_episode_launch_controls(
                channel_slug="test_ch",
                ch=ch_root,
                episodes_root=ep_root,
                episode_by_name={},
                episode_names=[],
                playlist_key="studio_playlist_test_ch",
                playlist_ids=["standalone"],
                playlist_labels=["Standalone"],
                write_nav_to_url=lambda: None,
                remember_episode_title=lambda *a, **k: None,
                rename_playlist=lambda *a, **k: None,
                next_manual_standalone_id=lambda c: "EP01",
                ensure_episode_files=lambda *a, **k: None,
                load_channel_backlog=lambda p: sys.modules["pandas"].DataFrame(),
            )
        finally:
            st.button = orig_button


if __name__ == "__main__":
    test_no_nested_expanders_ast()
    test_trim_media_ffmpeg_smoke()
    test_dim_during_speech_propagation()
    test_mux_stage_requires_tail_video_when_tail_enabled()
    test_studio_blocks_no_nested_expanders_runtime()
    print("studio refactor tests ok")

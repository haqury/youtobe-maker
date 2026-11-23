"""Запуск локального ComfyUI из веб-панели (Windows / фоновый процесс)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from comfyui_client import comfyui_base_url, is_comfyui_online

LOG_DIR = ROOT / "logs" / "comfyui"
PID_PATH = LOG_DIR / "comfyui.pid"
LOG_PATH = LOG_DIR / "comfyui_server.log"

DEFAULT_COMFY_ROOT = Path(r"C:\Users\haqury\comfyui-amd-windows-setup\ComfyUI")


def _load_env() -> None:
    load_dotenv(ROOT / ".env")


def comfyui_root() -> Path:
    _load_env()
    raw = os.getenv("COMFYUI_ROOT", str(DEFAULT_COMFY_ROOT)).strip()
    return Path(raw).expanduser().resolve()


def resolve_python() -> str:
    _load_env()
    explicit = os.getenv("COMFYUI_PYTHON", "").strip().strip('"')
    if explicit:
        return explicit
    preset = os.getenv("COMFYUI_CONDA_ENV", "comfy311").strip()
    if preset and shutil.which("conda"):
        conda_exe = shutil.which("conda") or "conda"
        try:
            proc = subprocess.run(
                [conda_exe, "run", "-n", preset, "python", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=60,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                candidate = proc.stdout.strip().splitlines()[-1].strip()
                if candidate and Path(candidate).is_file():
                    return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    return sys.executable


def _python_has_directml(python: str) -> bool:
    try:
        proc = subprocess.run(
            [python, "-c", "import torch_directml"],
            capture_output=True,
            timeout=30,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def launch_mode(python: str | None = None) -> str:
    _load_env()
    mode = os.getenv("COMFYUI_LAUNCH_MODE", "auto").strip().lower()
    if mode in {"cpu", "directml"}:
        return mode
    py = python or resolve_python()
    return "directml" if _python_has_directml(py) else "cpu"


def build_launch_command(python: str | None = None) -> list[str]:
    _load_env()
    root = comfyui_root()
    main_py = root / "main.py"
    if not main_py.is_file():
        raise FileNotFoundError(f"ComfyUI main.py не найден: {main_py}")
    py = python or resolve_python()
    port = os.getenv("COMFYUI_PORT", "8188").strip() or "8188"
    mode = launch_mode(py)
    cmd = [py, str(main_py), "--listen", "0.0.0.0", "--port", port]
    if mode == "directml":
        cmd.extend(["--directml", "--force-fp32"])
    else:
        cmd.append("--cpu")
    return cmd


def _read_pid() -> int | None:
    if not PID_PATH.is_file():
        return None
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
            return str(pid) in (out.stdout or "")
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_log_tail(lines: int = 40) -> str:
    if not LOG_PATH.is_file():
        return ""
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    chunk = text.splitlines()
    return "\n".join(chunk[-lines:])


def comfyui_status() -> dict[str, Any]:
    _load_env()
    base = comfyui_base_url()
    online = is_comfyui_online(base, timeout=2.0)
    pid = _read_pid()
    proc_alive = pid is not None and _pid_alive(pid)
    python = resolve_python()
    mode = launch_mode(python)
    root = comfyui_root()
    main_ok = (root / "main.py").is_file()
    label = "🟢 ComfyUI онлайн" if online else "⚪ ComfyUI оффлайн"
    if not online and proc_alive:
        label = "🟡 ComfyUI запускается…"
    detail_parts: list[str] = [f"URL: {base}", f"Python: {python}", f"Режим: {mode}"]
    if not main_ok:
        detail_parts.append(f"Нет main.py в {root}")
    if mode == "cpu":
        detail_parts.append(
            "GPU/DirectML недоступен — задайте COMFYUI_PYTHON (Python 3.11 + torch-directml) "
            "или COMFYUI_CONDA_ENV=comfy311"
        )
    return {
        "online": online,
        "label": label,
        "pid": pid,
        "process_alive": proc_alive,
        "python": python,
        "mode": mode,
        "root": str(root),
        "main_ok": main_ok,
        "detail": " · ".join(detail_parts),
        "log_tail": read_log_tail(),
    }


def start_comfyui_server() -> tuple[bool, str]:
    _load_env()
    status = comfyui_status()
    if status["online"]:
        return True, "ComfyUI уже работает."
    if not status["main_ok"]:
        return False, f"Не найден ComfyUI: {status['root']}"

    pid = status["pid"]
    if pid and status["process_alive"]:
        return True, "Процесс ComfyUI уже стартует — подождите и обновите страницу."

    try:
        cmd = build_launch_command()
    except FileNotFoundError as exc:
        return False, str(exc)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.write(" ".join(cmd) + "\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(comfyui_root()),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            return False, f"Не удалось запустить ComfyUI: {exc}"

    PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    mode = launch_mode(resolve_python())
    hint = "DirectML (AMD)" if mode == "directml" else "CPU (медленно; для GPU — окружение 3.11)"
    return True, f"ComfyUI запущен (pid {proc.pid}, {hint}). Через 30–90 с должен подняться API."


def stop_comfyui_server() -> tuple[bool, str]:
    pid = _read_pid()
    if not pid:
        return False, "PID ComfyUI не сохранён — закройте окно процесса вручную."
    if not _pid_alive(pid):
        PID_PATH.unlink(missing_ok=True)
        return True, "Процесс уже завершён."
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, timeout=15)
        else:
            os.kill(pid, 15)
    except OSError as exc:
        return False, f"Не удалось остановить pid {pid}: {exc}"
    PID_PATH.unlink(missing_ok=True)
    return True, f"ComfyUI остановлен (pid {pid})."


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ComfyUI launcher для youtobe-maker")
    parser.add_argument("action", choices=("start", "stop", "status"), nargs="?", default="status")
    args = parser.parse_args(argv)
    if args.action == "start":
        ok, msg = start_comfyui_server()
        print(msg)
        return 0 if ok else 1
    if args.action == "stop":
        ok, msg = stop_comfyui_server()
        print(msg)
        return 0 if ok else 1
    status = comfyui_status()
    print(status["label"])
    print(status["detail"])
    if status["log_tail"]:
        print("--- log tail ---")
        print(status["log_tail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
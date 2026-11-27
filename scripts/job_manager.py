"""Фоновые задачи: запуск, прогресс, история, остановка."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from stdio_utf8 import subprocess_env

LOGS_DIR = ROOT / "logs"
HISTORY_PATH = LOGS_DIR / "jobs_history.json"
PROGRESS_RE = re.compile(r"^\[PROGRESS\]\s+(\d+)\/(\d+)\s+\|\s*(.+)$")

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.is_file():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_history(jobs: list[dict[str, Any]]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_job(jobs: list[dict[str, Any]], job_id: str) -> dict[str, Any] | None:
    for job in jobs:
        if job.get("id") == job_id:
            return job
    return None


def _get_job(job_id: str) -> dict[str, Any] | None:
    return _find_job(_load_history(), job_id)


def _update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        jobs = _load_history()
        job = _find_job(jobs, job_id)
        if not job:
            return
        job.update(fields)
        _save_history(jobs)


def _parse_progress_line(line: str) -> tuple[float, str] | None:
    match = PROGRESS_RE.match(line.strip())
    if not match:
        return None
    current = int(match.group(1))
    total = int(match.group(2))
    text = match.group(3).strip()
    ratio = current / max(total, 1)
    return min(1.0, max(0.0, ratio)), text


def _finalize_job(job_id: str, process: subprocess.Popen[Any]) -> None:
    try:
        process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait(timeout=10)
    with _lock:
        jobs = _load_history()
        job = _find_job(jobs, job_id)
        if not job:
            return
        if job.get("status") == "stopped":
            return
        code = process.returncode
        log_path = ROOT / str(job.get("log_file", ""))
        success_log = _log_indicates_success(log_path)
        status = "done" if code == 0 or (code in (None, -1, 1, 15) and success_log) else "error"
        job["status"] = status
        job["finished_at"] = _now_iso()
        if status == "done":
            job["progress"] = 1.0
            job["step_text"] = "Завершено"
        else:
            log_path = ROOT / str(job.get("log_file", ""))
            brief = f"Ошибка, код {code}"
            if log_path.is_file():
                try:
                    lines = [
                        ln.strip()
                        for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip()
                    ]
                    for line in reversed(lines):
                        if line.startswith("[ERROR]") or "Error" in line or "Traceback" in line:
                            brief = line[:220]
                            break
                    else:
                        if lines:
                            brief = lines[-1][:220]
                except OSError:
                    pass
            job["step_text"] = brief
        _save_history(jobs)


def _log_indicates_success(log_path: Path) -> bool:
    if not log_path.is_file():
        return False
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
    except OSError:
        return False
    if "Готово: этап" in tail:
        return True
    return "Готово:" in tail and ("video ready" in tail or "final_video.mp4" in tail)


def _tail_log_loop(job_id: str, log_path: Path, process: subprocess.Popen[Any]) -> None:
    last_pos = 0
    success_seen_at: float | None = None
    while True:
        chunk = ""
        if log_path.is_file():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(last_pos)
                    chunk = handle.read()
                    last_pos = handle.tell()
            except OSError:
                chunk = ""
        if chunk:
            for line in chunk.splitlines():
                parsed = _parse_progress_line(line)
                if parsed:
                    progress, step_text = parsed
                    _update_job(job_id, progress=progress, step_text=step_text)
        if _log_indicates_success(log_path):
            if success_seen_at is None:
                success_seen_at = time.time()
            elif time.time() - success_seen_at > 45 and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                break
        if process.poll() is not None:
            if log_path.is_file():
                try:
                    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(last_pos)
                        tail = handle.read()
                    for line in tail.splitlines():
                        parsed = _parse_progress_line(line)
                        if parsed:
                            progress, step_text = parsed
                            _update_job(job_id, progress=progress, step_text=step_text)
                except OSError:
                    pass
            break
        time.sleep(0.4)
    _finalize_job(job_id, process)


def start_job(
    job_type: str,
    episode_path: str | Path,
    command: list[str],
    *,
    script_name: str | None = None,
    script_args: list[str] | None = None,
) -> str:
    """Запускает процесс в фоне, пишет stdout/stderr в logs/job_<id>.log."""
    episode_path = str(Path(episode_path).resolve())
    job_id = uuid.uuid4().hex[:12]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"job_{job_id}.log"

    job: dict[str, Any] = {
        "id": job_id,
        "type": job_type,
        "episode": episode_path,
        "status": "running",
        "progress": 0.0,
        "step_text": "Запуск…",
        "started_at": _now_iso(),
        "finished_at": None,
        "log_file": str(log_file.relative_to(ROOT)).replace("\\", "/"),
        "pid": None,
    }
    if script_name:
        job["script_name"] = script_name
    if script_args is not None:
        job["script_args"] = [str(a) for a in script_args]

    with _lock:
        jobs = _load_history()
        jobs.append(job)
        _save_history(jobs)

    log_handle = log_file.open("w", encoding="utf-8", errors="replace")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=subprocess_env(),
        creationflags=creationflags,
    )
    log_handle.close()
    _update_job(job_id, pid=process.pid)

    thread = threading.Thread(target=_tail_log_loop, args=(job_id, log_file, process), daemon=True)
    thread.start()
    return job_id


def get_jobs(limit: int | None = 50) -> list[dict[str, Any]]:
    jobs = sorted(_load_history(), key=lambda row: row.get("started_at", ""), reverse=True)
    if limit:
        return jobs[:limit]
    return jobs


def get_job_details(job_id: str) -> dict[str, Any] | None:
    job = _get_job(job_id)
    if not job:
        return None
    log_path = ROOT / str(job.get("log_file", ""))
    log_tail = ""
    if log_path.is_file():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        log_tail = text[-12000:] if len(text) > 12000 else text
    return {**job, "log_tail": log_tail}


def stop_job(job_id: str) -> bool:
    job = _get_job(job_id)
    if not job or job.get("status") != "running":
        return False
    pid = job.get("pid")
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            import os
            import signal

            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    _update_job(job_id, status="stopped", finished_at=_now_iso(), step_text="Остановлено пользователем")
    return True


def start_script_job(job_type: str, episode_path: str | Path, script_name: str, *args: str) -> str:
    command = [sys.executable, "-u", str(ROOT / "scripts" / script_name), *args]
    return start_job(
        job_type,
        episode_path,
        command,
        script_name=script_name,
        script_args=list(args),
    )


def episode_has_running_job(episode_path: str | Path) -> bool:
    episode_resolved = str(Path(episode_path).resolve())
    return any(
        row.get("status") == "running" and row.get("episode") == episode_resolved
        for row in get_jobs(limit=200)
    )


def job_can_restart(job: dict[str, Any] | None) -> bool:
    if not job:
        return False
    if job.get("status") not in {"error", "stopped"}:
        return False
    return bool(job.get("script_name"))


def restart_job(job_id: str) -> tuple[str | None, str | None]:
    """
    Повторный запуск той же команды. Возвращает (new_job_id, error_message).
  """
    job = _get_job(job_id)
    if not job:
        return None, "Задача не найдена."
    if not job_can_restart(job):
        if job.get("status") == "running":
            return None, "Задача ещё выполняется."
        if not job.get("script_name"):
            return None, "Нет данных для перезапуска (старая запись до обновления)."
        return None, "Эту задачу нельзя перезапустить."
    episode = str(job.get("episode") or "")
    if episode and episode_has_running_job(episode):
        return None, "Уже выполняется фоновая задача для этого эпизода."
    script_name = str(job["script_name"])
    raw_args = job.get("script_args")
    args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
    if script_name == "sync_episode_pack.py" and episode:
        ep_dir = Path(episode)
        pending = ep_dir / ".sync_instruction.txt"
        last = ep_dir / "sync_instruction_last.txt"
        if not pending.is_file() and last.is_file():
            try:
                pending.write_text(last.read_text(encoding="utf-8-sig"), encoding="utf-8")
            except OSError:
                pass
    job_type = str(job.get("type") or "generation")
    new_id = start_script_job(job_type, episode, script_name, *args)
    _update_job(new_id, restarted_from=job_id)
    return new_id, None


def jobs_for_episode(episode_path: str | Path, limit: int = 30) -> list[dict[str, Any]]:
    episode_resolved = str(Path(episode_path).resolve())
    return [job for job in get_jobs(limit=200) if job.get("episode") == episode_resolved][:limit]
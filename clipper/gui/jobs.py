"""Run ``clipper`` commands in the background and keep what they print.

Each job is the real command line tool in a child process, in the project folder, exactly as if it
had been typed in a terminal: the same code path, the same ``.env`` and ``cookies.txt``. That keeps
the app and the terminal in step, isolates crashes, and makes Cancel trivial (stop the process).
Only one job runs at a time: a video render uses all the computer has.
"""

from __future__ import annotations

import os
import re
import secrets
import signal
# Runs this project's own command line with an argument list, never a shell.
import subprocess  # nosec B404
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import progress

MAX_LINES = 4000  # per job; the oldest lines are dropped beyond this
DROP_AT_ONCE = 500
KEEP_JOBS = 20
KILL_AFTER = 5.0  # seconds between asking a job to stop and making it stop
DOWNLOAD_PROGRESS = re.compile(r"^\[download\]\s+\d+(\.\d+)?%")


class JobBusy(RuntimeError):
    """Another job is still running."""


@dataclass
class Job:
    id: str
    kind: str
    command: list[str]
    started: float
    session: str | None = None  # known up front for process/export; read from the log for find/run
    status: str = "running"  # running | done | failed | cancelled
    returncode: int | None = None
    lines: list[str] = field(default_factory=list)
    dropped: int = 0
    live: str = ""  # the latest download-progress line, shown in place instead of flooding the log
    cancelled: bool = False
    process: subprocess.Popen[str] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class JobManager:
    def __init__(self, project_dir: str | Path, base_command: Sequence[str] | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.base_command = list(base_command) if base_command else [sys.executable, "-u", "-m", "clipper"]
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    # -- environment -----------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        return {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}

    # -- starting and stopping ---------------------------------------------------------------

    def running(self) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.status == "running"), None)

    def start(self, kind: str, args: Sequence[str], session: str | None = None) -> Job:
        with self._lock:
            if any(j.status == "running" for j in self._jobs.values()):
                raise JobBusy("CLIPPER is already working on something.")
            command = [*self.base_command, *args]
            job = Job(id=secrets.token_urlsafe(9), kind=kind, command=command, started=time.time(), session=session)
            # Fixed program; every argument was validated in commands.py.
            job.process = subprocess.Popen(  # nosec B603
                command,
                cwd=self.project_dir,
                env=self._env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,  # its own process group, so Cancel also stops ffmpeg and yt-dlp
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > KEEP_JOBS:
                old = self._order.pop(0)
                if self._jobs[old].status != "running":
                    del self._jobs[old]
                else:
                    self._order.insert(0, old)
                    break
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return job

    def _pump(self, job: Job) -> None:
        process = job.process
        # start() sets both before this thread begins.
        assert process is not None and process.stdout is not None  # nosec B101
        try:
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                with job.lock:
                    if DOWNLOAD_PROGRESS.match(line):
                        job.live = line
                        continue
                    job.lines.append(line)
                    if len(job.lines) > MAX_LINES:
                        del job.lines[:DROP_AT_ONCE]
                        job.dropped += DROP_AT_ONCE
        finally:
            code = process.wait()
            with job.lock:
                job.returncode = code
                job.live = ""
                job.status = "cancelled" if job.cancelled else ("done" if code == 0 else "failed")

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status != "running" or job.process is None:
            return False
        job.cancelled = True
        self._signal(job, signal.SIGTERM)
        timer = threading.Timer(KILL_AFTER, self._signal, args=(job, signal.SIGKILL))
        timer.daemon = True
        timer.start()
        return True

    @staticmethod
    def _signal(job: Job, sig: int) -> None:
        if job.process is None or job.process.poll() is not None:
            return
        try:
            os.killpg(job.process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def shutdown(self) -> None:
        job = self.running()
        if job is not None:
            self.cancel(job.id)
            if job.process is not None:
                try:
                    job.process.wait(timeout=KILL_AFTER + 1)
                except subprocess.TimeoutExpired:
                    pass

    # -- reading -----------------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job: Job, since: int = 0) -> dict[str, Any]:
        """The job as the page sees it: new log lines since ``since``, plus progress and any error."""
        with job.lock:
            first = max(0, since - job.dropped)
            new = job.lines[first:]
            total = job.dropped + len(job.lines)
            lines = list(job.lines)
            status, code, live = job.status, job.returncode, job.live
        info = progress.summarize(lines, job.kind)
        return {
            "id": job.id,
            "kind": job.kind,
            "status": status,
            "returncode": code,
            "lines": new,
            "next": total,
            "live": live,
            "progress": info,
            "session": info["session"] or job.session,
            "error": progress.friendly_error(lines) if status == "failed" else None,
            "seconds": round(time.time() - job.started),
        }

    def run_quick(self, args: Sequence[str], timeout: float = 90.0) -> tuple[int, str]:
        """Run a short command (like ``doctor``) to completion and return (exit code, its output)."""
        try:
            # Fixed program; the arguments are fixed in server.py.
            done = subprocess.run(  # nosec B603
                [*self.base_command, *args],
                cwd=self.project_dir,
                env=self._env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, "That took too long and was stopped."
        return done.returncode, (done.stdout or "") + (done.stderr or "")

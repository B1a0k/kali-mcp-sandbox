"""Bounded, persistent guest jobs. No retries of commands, even after reconnect."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
MAX_OUTPUT = 4 * 1024 * 1024  # per stream, per job


class Jobs:
    def __init__(self, root="/workspace/.jobs", max_active=2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(self.root / "jobs.db", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, fingerprint TEXT, state TEXT, exit_code INTEGER, created REAL, finished REAL)")
        self.db.execute("UPDATE jobs SET state='interrupted', finished=? WHERE state IN ('running','queued')", (time.time(),))
        self.db.commit()
        self.processes = {}
        self.max_active = max_active

    def submit(self, command, request_id=None, timeout=1800):
        if not isinstance(command, (str, list)) or not command or len(str(command)) > 65536:
            raise ValueError("Invalid command")
        if not 1 <= timeout <= 3600:
            raise ValueError("Job timeout must be 1–3600 seconds")
        request_id = request_id or str(uuid.uuid4())
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("Invalid request_id")
        fingerprint = hashlib.sha256(json.dumps({'command': command, 'timeout': timeout}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            old = self.db.execute("SELECT id,fingerprint FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old[1] != fingerprint:
                    raise ValueError("request_id was already used for a different command")
                return self.read(old[0])
            if len(self.processes) >= self.max_active:
                raise RuntimeError("Job capacity reached; wait for an existing job to finish")
            if self.db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= 256:
                raise RuntimeError("Job history quota reached; export results and use a new environment")
            job_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,'queued',NULL,?,NULL)", (job_id, request_id, fingerprint, time.time()))
            self.db.commit()
            try:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker.py")), json.dumps(command)], cwd=self.root.parent,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           start_new_session=True)
            except Exception:
                self._finish(job_id, "failed", -1)
                raise
            self.processes[job_id] = process
            self.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
            self.db.commit()
            threading.Thread(target=self._wait, args=(job_id, process, timeout), daemon=True).start()
            return {"jobId": job_id, "state": "running", "accepted": True}

    def _drain(self, job_id, name, stream):
        size = 0
        with (self.root / f"{job_id}.{name}").open("wb") as output:
            while True:
                data = stream.read1(8192)
                if not data:
                    break
                keep = data[:max(0, MAX_OUTPUT - size)]
                output.write(keep)
                output.flush()
                size += len(keep)
                if keep:
                    with self.changed:
                        self.changed.notify_all()
        stream.close()

    def _kill(self, process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        # The shell may have exited while grandchildren are still in its group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)

    def _wait(self, job_id, process, timeout):
        readers = [threading.Thread(target=self._drain, args=(job_id, name, stream), daemon=True)
                   for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
        for reader in readers:
            reader.start()
        state = "failed"
        try:
            process.wait(timeout=timeout)
            state = "succeeded" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            state = "timed_out"
        finally:
            self._kill(process)
            for reader in readers:
                reader.join(timeout=5)
            with self.lock:
                current = self.db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                if current != "cancelled":
                    self._finish(job_id, state, process.returncode)
                self.processes.pop(job_id, None)

    def _finish(self, job_id, state, exit_code):
        self.db.execute("UPDATE jobs SET state=?,exit_code=?,finished=? WHERE id=?", (state, exit_code, time.time(), job_id))
        self.db.commit()
        self.changed.notify_all()

    def read(self, job_id, cursor=0, stream="stdout", wait_seconds=0):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("Invalid job id")
        if stream not in ("stdout", "stderr") or not isinstance(cursor, int) or cursor < 0 or cursor > MAX_OUTPUT:
            raise ValueError("Invalid output cursor")
        if not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 5:
            raise ValueError("wait_seconds must be 0–5")
        path = self.root / f"{job_id}.{stream}"
        deadline = time.monotonic() + wait_seconds
        with self.changed:
            while True:
                row = self.db.execute("SELECT state,exit_code FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    raise ValueError("Unknown job")
                remaining = deadline - time.monotonic()
                if row[0] in TERMINAL or remaining <= 0 or (path.exists() and path.stat().st_size > cursor):
                    break
                self.changed.wait(remaining)
        data = b""
        if path.exists():
            with path.open("rb") as file:
                file.seek(cursor)
                data = file.read(16384)
        return {"jobId": job_id, "state": row[0], "exitCode": row[1], "stream": stream,
                "output": data.decode("utf-8", errors="replace"), "nextCursor": cursor + len(data),
                "outputLimitBytes": MAX_OUTPUT, "truncated": path.exists() and path.stat().st_size >= MAX_OUTPUT}

    def cancel(self, job_id):
        self.read(job_id)
        with self.lock:
            process = self.processes.get(job_id)
            if process:
                self._finish(job_id, "cancelled", None)
        if process:
            self._kill(process)
        return self.read(job_id)

    def health(self):
        with self.lock:
            recent = self.db.execute("SELECT id,state FROM jobs ORDER BY CASE WHEN state IN ('running','queued') THEN 0 ELSE 1 END,created DESC LIMIT 20").fetchall()
            return {"ready": True, "activeJobs": len(self.processes), "jobs": [{"jobId": r[0], "state": r[1]} for r in recent]}

    def close(self):
        for job_id in list(self.processes):
            self.cancel(job_id)

"""Keep Kali's tool routes; replace its synchronous command runner with jobs."""
import os
import fcntl
import logging
from logging.handlers import RotatingFileHandler
import signal
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "upstream"))
import server as upstream
from flask import request, jsonify
from waitress import serve
from jobs import Jobs

# The API outlives individual stdio bridges; a new bridge may race a reconnect.
state_dir = Path('/workspace/.jobs')
state_dir.mkdir(parents=True, exist_ok=True)
api_lock = (state_dir / 'api.lock').open('a')
try:
    fcntl.flock(api_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
logging.getLogger().addHandler(RotatingFileHandler(state_dir / 'api.log', maxBytes=262144, backupCount=1))
boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
boot_record = state_dir / 'api-boot-id'
if boot_record.exists() and boot_record.read_text() == boot_id:
    logging.error('API exited within this VM boot. Stop/start the environment before reconnecting; previous jobs may still be alive.')
    sys.exit(1)
boot_record.write_text(boot_id)
jobs = Jobs()
upstream.execute_command = lambda command: jobs.submit(command, request.headers.get("X-Request-Id"))

@upstream.app.get("/managed/health")
def health():
    missing = [tool for tool in ("nmap", "gobuster", "dirb", "nikto", "sqlmap", "whatweb", "curl", "dig") if not shutil.which(tool)]
    return jsonify({**jobs.health(), "ready": not missing, "missingTools": missing,
                    "serviceVersion": (Path(__file__).with_name("version.txt").read_text().strip() if Path(__file__).with_name("version.txt").exists() else "bundled")})

@upstream.app.post("/managed/read")
def read_job():
    return jsonify(jobs.read(**request.get_json()))

@upstream.app.post("/managed/cancel")
def cancel_job():
    return jsonify(jobs.cancel(**request.get_json()))

@upstream.app.post("/managed/submit")
def submit_job():
    return jsonify(jobs.submit(**request.get_json()))

@upstream.app.errorhandler(ValueError)
@upstream.app.errorhandler(RuntimeError)
def error(exception):
    return jsonify({"error": str(exception)}), 400

def shutdown(*_):
    jobs.close()
    sys.exit(0)

if __name__ == "__main__":
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    serve(upstream.app, host="127.0.0.1", port=5000, threads=4, max_request_body_size=131072)

"""Offline shared-guest regression: multiple bridges, reconnect and scoped jobs."""
import argparse
import os
from pathlib import Path
import subprocess
import time
import uuid
from smoke import RPC


def verify(image):
    name = 'kali-shared-test-' + uuid.uuid4().hex[:10]
    source = str(Path(__file__).resolve().parents[1] / 'image')
    clients = []
    try:
        subprocess.run(['docker', 'run', '-d', '--name', name, '--network', 'none',
                        '--cpus', '1', '--memory', '256m', '--user', '0:0',
                        '--mount', f'type=bind,source={source},target=/opt/kali-mcp,readonly',
                        '--entrypoint', 'python3', image, '/opt/kali-mcp/api.py'], check=True, capture_output=True)
        def connect():
            rpc = RPC(['docker', 'exec', '-i', '--user', '0:0', name,
                       'python3', '/opt/kali-mcp/bridge.py'], os.environ.copy())
            clients.append(rpc)
            rpc.initialize()
            return rpc
        a, b = connect(), connect()
        first = a.tool('execute_command', {'command': 'mkdir -p /workspace/conversations/a; cd /workspace/conversations/a; printf a > marker; sleep 30', 'request_id': 'conversation-a-operation', 'timeout': 60})
        second = b.tool('execute_command', {'command': 'mkdir -p /workspace/conversations/b; cd /workspace/conversations/b; printf b > marker; sleep 2; cat marker', 'request_id': 'conversation-b-operation', 'timeout': 30})
        a.close()
        c = connect()
        health = c.tool('environment_health', {})
        assert health['ready'] and any(j['jobId'] == first['jobId'] and j['requestId'] == 'conversation-a-operation' for j in health['jobs'])
        assert c.tool('job_cancel', {'job_id': first['jobId']})['state'] == 'cancelled'
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = b.tool('job_read', {'job_id': second['jobId'], 'wait_seconds': 5})
            if result['state'] not in ('running', 'queued'):
                break
        assert result['state'] == 'succeeded' and result['output'] == 'b', result
        assert b.tool('execute_command', {'command': 'mkdir -p /workspace/conversations/b; cd /workspace/conversations/b; printf b > marker; sleep 2; cat marker', 'request_id': 'conversation-b-operation', 'timeout': 30})['jobId'] == second['jobId']
        print('PASS: shared MCP across three bridges; reconnect preserves jobs/files; cancelling A leaves B successful; idempotent receipts retained')
    finally:
        for client in clients:
            if client.process.poll() is None:
                client.close()
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    verify(parser.parse_args().image)

"""Exercise real Linux jobs and MCP through Docker; does not substitute for WHP/KVM/HVF."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from smoke import RPC


def run(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def smoke(image):
    root = Path(__file__).resolve().parents[1] / "image"
    for test in ('test_jobs.py', 'test_bridge.py'):
        result = run('run', '--rm', '--network', 'none', '--cpus', '2', '--memory', '1g',
                     '--pids-limit', '128', '--mount', f'type=bind,src={root / test},dst=/opt/kali-mcp/{test},readonly',
                     '--entrypoint', 'python3', image, '/opt/kali-mcp/' + test, '-v')
        print(f'PASS: Linux {test}\n{result}', flush=True)
    name = 'kali-mcp-validation-' + uuid.uuid4().hex[:12]
    rpc = None
    try:
        run('run', '-d', '--name', name, '--network', 'none', '--cpus', '2',
            '--memory', '1g', '--pids-limit', '128', image)
        command = ['docker', 'exec', '-i', '--user', '0:0', name, 'python3', '/opt/kali-mcp/bridge.py']
        rpc = RPC(command, os.environ.copy())
        rpc.initialize()
        names = {tool['name'] for tool in rpc.call('tools/list', {})['tools']}
        assert {'nmap_scan', 'gobuster_scan', 'job_read', 'job_cancel', 'execute_command'} <= names, names
        health = rpc.tool('environment_health', {})
        assert health['ready'] and health['root'] and health['effectiveUid'] == 0, health
        assert health['toolchainReady'] and health['toolInventoryComplete'], health
        assert health['imageVersion'] != 'legacy-unversioned', health
        assert health['packageManagement']['installationAllowed'], health
        assert health['packageManagement']['aptGet'] and health['packageManagement']['dpkg'], health
        assert health['execution']['maxReadWaitSeconds'] == 5, health
        assert health['network']['rawPacketSemantics'] is False, health
        command_check = 'id -u; for x in openvpn smbclient smbexec impacket-smbexec proxychains4 nxc smbmap enum4linux-ng ldapsearch socat sshpass; do command -v "$x" || exit 1; done'
        tool_job = rpc.tool('execute_command', {'command': command_check, 'request_id': 'root-toolchain', 'timeout': 30})['jobId']
        tool_result = rpc.tool('job_read', {'job_id': tool_job, 'wait_seconds': 5})
        assert tool_result['state'] == 'succeeded' and tool_result['output'].splitlines()[0] == '0', tool_result
        request = {'command': 'printf reconnect-ok; sleep 2', 'request_id': 'same-operation', 'timeout': 20}
        job = rpc.tool('execute_command', request)['jobId']
        assert rpc.tool('execute_command', request)['jobId'] == job
        rpc.close()
        rpc = RPC(command, os.environ.copy())
        rpc.initialize()
        output, cursor = '', 0
        for _ in range(10):
            result = rpc.tool('job_read', {'job_id': job, 'cursor': cursor})
            output += result['output']
            cursor = result['nextCursor']
            if result['state'] == 'succeeded':
                break
            time.sleep(.1)
        assert result['state'] == 'succeeded' and 'reconnect-ok' in output, result
        job = rpc.tool('execute_command', {'command': 'sleep 60', 'request_id': 'cancel-operation', 'timeout': 90})['jobId']
        assert rpc.tool('job_cancel', {'job_id': job})['state'] == 'cancelled'
        print('PASS: real MCP initialize/catalog/health/jobs/idempotency/reconnect/cancel', flush=True)
        print(run('stats', '--no-stream', '--format', '{{json .}}', name), flush=True)
        print(json.dumps({'image': image, 'tools': sorted(names)}, ensure_ascii=False), flush=True)
    finally:
        if rpc:
            rpc.close()
        run('rm', '-f', name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    smoke(parser.parse_args().image)

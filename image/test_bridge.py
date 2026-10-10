"""Protocol/schema tests without a VM. These do not replace the VM smoke gate."""
import asyncio
import unittest
from unittest.mock import patch
import bridge
import requests
import capabilities
from mcp.server.fastmcp.exceptions import ToolError

class BridgeTests(unittest.TestCase):
    def test_catalog_matches_installed_tools(self):
        class Client:
            def safe_post(self, endpoint, data):
                return {"jobId": "id", "state": "running", "endpoint": endpoint, "data": data}
        with patch.object(bridge.upstream, "FastMCP", bridge.AvailableMCP), patch.object(bridge.shutil, "which", side_effect=lambda name: name if name in ("nmap", "gobuster") else None):
            mcp = bridge.upstream.setup_mcp_server(Client())
        tools = asyncio.run(mcp.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(names, {"nmap_scan", "gobuster_scan"})
        nmap = next(tool for tool in tools if tool.name == "nmap_scan")
        self.assertEqual(nmap.inputSchema["properties"]["scan_type"]["default"], "-sT -Pn -sV")
        self.assertIn("asynchronous jobId", nmap.description)

    def test_local_api_does_not_inherit_host_proxy(self):
        self.assertFalse(bridge.Client().session.trust_env)

    def test_job_contract_and_permissions(self):
        with patch.object(bridge.shutil, 'which', return_value=None):
            server = bridge.build_server(object())
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertEqual(set(tools), {'environment_health', 'execute_command', 'job_read', 'job_cancel'})
        self.assertTrue(tools['job_read'].annotations.readOnlyHint)
        self.assertFalse(tools['execute_command'].annotations.readOnlyHint)
        self.assertFalse(tools['job_cancel'].annotations.readOnlyHint)
        self.assertIn('request_id', tools['execute_command'].inputSchema['required'])
        self.assertEqual(tools['job_read'].inputSchema['properties']['wait_seconds']['default'], 5)
        self.assertEqual(tools['job_read'].inputSchema['properties']['wait_seconds']['maximum'], 5)
        self.assertEqual(tools['job_read'].inputSchema['properties']['wait_seconds']['minimum'], 0)
        self.assertIn('as root', tools['execute_command'].description)

    def test_invalid_wait_is_rejected_before_http_submission(self):
        class Client:
            def safe_post(self, *_):
                raise AssertionError('Invalid MCP arguments must not reach HTTP')
        with patch.object(bridge.shutil, 'which', return_value=None):
            server = bridge.build_server(Client())
        with self.assertRaisesRegex(ToolError, 'less than or equal to 5'):
            asyncio.run(server.call_tool('job_read', {'job_id': 'a' * 32, 'wait_seconds': 10}))

    def test_http_error_preserves_actionable_api_body(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"error":"wait_seconds must be 0-5"}'
        client = bridge.Client()
        with patch.object(client.session, 'post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'managed/read: HTTP 400.*wait_seconds must be 0-5'):
                client.safe_post('managed/read', {})

    def test_health_does_not_replace_full_live_binary_inventory(self):
        class Client:
            def ready(self):
                return {'ready': True, 'tools': ['openvpn', 'nxc'], 'missingTools': ['smbclient']}
        with patch.object(bridge.shutil, 'which', return_value=None):
            server = bridge.build_server(Client())
        result = asyncio.run(server.call_tool('environment_health', {}))
        self.assertIn('openvpn', str(result))
        self.assertIn('nxc', str(result))
        self.assertIn('smbclient', str(result))

    def test_inventory_tracks_newly_installed_commands(self):
        present = {'nmap', 'apt-get', 'dpkg'}
        with patch.object(capabilities.shutil, 'which', side_effect=lambda tool: '/usr/bin/' + tool if tool in present else None), patch.object(capabilities.os, 'geteuid', return_value=0, create=True):
            before = capabilities.inventory()
            self.assertIn('openvpn', before['missingTools'])
            self.assertTrue(before['packageManagement']['installationAllowed'])
            present.update({'openvpn', 'nxc', 'smbclient', 'proxychains4'})
            after = capabilities.inventory()
            self.assertIn('openvpn', after['tools'])
            self.assertNotIn('openvpn', after['missingTools'])

    def test_reconnect_does_not_restart_healthy_api(self):
        class Client:
            def ready(self):
                return {'ready': True}
        with patch.object(bridge.subprocess, 'Popen') as spawn:
            bridge.ensure_api(Client())
            spawn.assert_not_called()

if __name__ == "__main__":
    unittest.main()

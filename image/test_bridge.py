"""Protocol/schema tests without a VM. These do not replace the VM smoke gate."""
import asyncio
import unittest
from unittest.mock import patch
import bridge

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

    def test_reconnect_does_not_restart_healthy_api(self):
        class Client:
            def ready(self):
                return {'ready': True}
        with patch.object(bridge.subprocess, 'Popen') as spawn:
            bridge.ensure_api(Client())
            spawn.assert_not_called()

if __name__ == "__main__":
    unittest.main()

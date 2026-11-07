"""Safety recovery uses no AI or SMTP and survives a failed RPC."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import main


class SafetyRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_loop_survives_failure_and_retries_without_ai(self):
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = [ConnectionError("offline"), None]
        cfg = SimpleNamespace(supabase_url="http://localhost:54321",
                              supabase_service_role_key="test-only")
        with patch.object(main, "create_client", return_value=client), \
                patch.object(main.asyncio, "sleep", new=AsyncMock(
                    side_effect=[None, None, asyncio.CancelledError()])), \
                patch.object(main.log, "exception") as errors, \
                patch.object(main, "classify") as model:
            with self.assertRaises(asyncio.CancelledError):
                await main.safety_retry_loop(cfg)
        self.assertEqual(client.rpc.call_count, 2)
        client.rpc.assert_called_with("retry_safety_escalations", {})
        errors.assert_called_once()
        model.assert_not_called()

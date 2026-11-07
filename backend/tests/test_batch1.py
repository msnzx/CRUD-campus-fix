"""Batch 1 inference and worker fault injection; no external services."""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from test_routing import Database, config
from app import classify as routing, main, enrich


class SafetySignalTests(unittest.TestCase):
    def test_other_and_invalid_category_preserve_urgent_for_review(self):
        db = Database()
        self.addCleanup(db.http.close)
        for issue in ("Other", "Not in taxonomy"):
            routing._REF_CACHE = None
            with self.subTest(issue=issue), patch.object(routing,"_classify_with_deepseek",return_value=(issue,"URGENT")):
                result = routing.classify(db.ticket,db.client,config())
            self.assertTrue(result.needs_review)
            self.assertEqual((result.priority_id,result.confidence),(4,0.0))

    def test_unguarded_enrichment_makes_no_model_or_database_call(self):
        with patch.object(enrich,"_generate") as model:
            admin = MagicMock()
            self.assertFalse(enrich.enrich_ticket({"ticket_id":1},admin,issue_type_id=1,priority_id=1))
        model.assert_not_called()
        admin.rpc.assert_not_called()
        admin.table.assert_not_called()

    def test_enrichment_rejection_is_reported_without_direct_update(self):
        admin = MagicMock()
        admin.rpc.return_value.execute.return_value.data = False
        with patch.object(enrich,"_generate",return_value={"title":"Clean","description":"Original facts","explanation":None}):
            self.assertFalse(enrich.enrich_ticket({"ticket_id":1,"original_text":"Original facts"},admin,
                issue_type_id=None,priority_id=None,settings=config(),analysis_token="token",expected_revision=5))
        admin.table.assert_not_called()


class WorkerFaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_failure_isolated_and_all_database_work_is_off_loop(self):
        cfg, admin = config(), MagicMock()
        loop_thread = threading.get_ident()
        claims = iter([
            {"claimed":True,"claim_token":"a","ticket":{"ticket_id":1}},
            {"claimed":True,"claim_token":"b","ticket":{"ticket_id":2}},
            {"claimed":False,"reason":"empty"},
        ])
        def claim(*args):
            self.assertNotEqual(threading.get_ident(),loop_thread)
            return next(claims)
        processed=[]
        def process(claimed,*args):
            self.assertNotEqual(threading.get_ident(),loop_thread)
            ticket_id=claimed["ticket"]["ticket_id"]
            processed.append(ticket_id)
            if ticket_id==1:
                raise ConnectionError("Injected model/database failure")
            return main.ClassifyResult(ticket_id=2,routing_status="AUTO_ROUTED",detail="done")
        with patch.object(main,"create_client",return_value=admin), \
             patch.object(main.ai_jobs,"claim_next",side_effect=claim), \
             patch.object(main,"_run_claimed_ticket",side_effect=process), \
             patch.object(main.asyncio,"sleep",new=AsyncMock(side_effect=[None,asyncio.CancelledError()])), \
             self.assertLogs(main.log,level="ERROR"):
            with self.assertRaises(asyncio.CancelledError):
                await main.classification_loop(cfg)
        self.assertEqual(processed,[1,2])
        admin.table.assert_not_called()

    async def test_claim_outage_does_not_kill_worker(self):
        admin = MagicMock()
        with patch.object(main,"create_client",return_value=admin), \
             patch.object(main.ai_jobs,"claim_next",side_effect=[ConnectionError("offline"),{"claimed":False,"reason":"empty"}]) as claim, \
             patch.object(main.asyncio,"sleep",new=AsyncMock(side_effect=[None,asyncio.CancelledError()])), \
             self.assertLogs(main.log,level="ERROR"):
            with self.assertRaises(asyncio.CancelledError):
                await main.classification_loop(config())
        self.assertEqual(claim.call_count,2)

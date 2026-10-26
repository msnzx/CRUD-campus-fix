"""Local-worker deployment checks; no tunnel or hosted database required."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.update(
    SUPABASE_URL="https://deployment-test.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-publishable-key",
    SUPABASE_SERVICE_ROLE_KEY="",
    SMTP_HOST="",
    APP_URL="https://campusfizz.netlify.app",
)

from app.config import Settings
from app.main import app


class DeploymentTests(unittest.TestCase):
    def test_health_reports_degraded_state_without_service_role_key(self):
        with TestClient(app) as client:
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertFalse(response.json()["admin_enabled"])
        self.assertFalse(response.json()["ai_enabled"])

    def test_classification_endpoint_rejects_missing_authentication(self):
        with TestClient(app) as client:
            response = client.post("/api/tickets/1/classify")
        self.assertEqual(response.status_code, 401)

    def test_deployment_settings_keep_local_ollama_and_frontend_links(self):
        with patch.dict(os.environ, {
            "APP_URL": "https://campusfizz.netlify.app",
            "OLLAMA_URL": "http://localhost:11434/api/chat",
            "OLLAMA_MODEL": "deepseek-r1:8b",
        }):
            cfg = Settings(
                _env_file=None,
                supabase_url="https://deployment-test.supabase.co",
                supabase_publishable_key="test-publishable-key",
            )
        self.assertEqual(cfg.app_url, "https://campusfizz.netlify.app")
        self.assertEqual(cfg.ollama_url, "http://localhost:11434/api/chat")
        self.assertEqual(cfg.ollama_model, "deepseek-r1:8b")


if __name__ == "__main__":
    unittest.main()

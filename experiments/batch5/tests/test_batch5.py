"""Comprehensive test suite for Batch 5: provenance, active learning, dataset versioning, and experiments."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from experiments.batch5.active_learning import (
    ActiveLearningSelector, disagreement_score, entropy_normalized,
    margin_score, safety_risk_score
)
from experiments.batch5.adjudication import (
    adjudicate_correction, filter_approved_adjudications
)
from experiments.batch5.common import (
    AdjudicationError, Batch5Error, DatasetError, GateBlockedError, ProvenanceError,
    digest, jaccard_word_similarity, normalize_text
)
from experiments.batch5.correction import (
    build_correction, validate_correction
)
from experiments.batch5.dataset import (
    create_dataset_bundle, split_adjudicated_records, verify_dataset_bundle
)
from experiments.batch5.distillation import (
    DistillationConfig, combined_distillation_loss, kl_divergence,
    simulate_distillation, softmax_with_temperature
)
from experiments.batch5.fixtures import FIXTURE_CASES, load_synthetic_corrections
from experiments.batch5.formatters import (
    format_chatml, format_dataset_split, format_prompt_and_completion,
    format_target_json
)
from experiments.batch5.lora import (
    LoRAConfig, calculate_trainable_parameters, peft_adapter_config,
    simulate_lora_training
)


class TestCorrectionProvenance(unittest.TestCase):
    def test_build_and_validate_synthetic_correction(self):
        c = build_correction(**FIXTURE_CASES[0])
        validate_correction(c)
        self.assertEqual(c["origin"], "synthetic_fixture")
        self.assertEqual(c["status"], "PENDING_ADJUDICATION")
        self.assertTrue(c["correction_id"].startswith("corr-"))
        self.assertEqual(len(c["provenance_sha256"]), 64)

    def test_tampered_provenance_hash_is_detected(self):
        c = build_correction(**FIXTURE_CASES[0])
        c["report_text"] = "Tampered report text"
        with self.assertRaises(ProvenanceError):
            validate_correction(c)

    def test_rejects_real_world_origin_without_custodial_flag(self):
        case = dict(FIXTURE_CASES[0])
        case["origin"] = "real_world"
        with self.assertRaises(GateBlockedError):
            build_correction(**case)

    def test_detects_pii_in_report_text(self):
        case = dict(FIXTURE_CASES[0])
        case["report_text"] = "SYNTHETIC FIXTURE: Call me back at 555-123-4567 or email john@caldwell.edu"
        with self.assertRaises(GateBlockedError):
            build_correction(**case)

    def test_rejects_predating_correction_timestamp(self):
        case = dict(FIXTURE_CASES[0])
        staff = dict(case["staff_action"])
        staff["corrected_at"] = "2026-11-06T11:00:00+00:00"  # before routed_at (12:00:00)
        case["staff_action"] = staff
        with self.assertRaises(ProvenanceError):
            build_correction(**case)

    def test_disagreement_detection_and_urgency_escalation(self):
        # Case 5003 is medium -> urgent escalation
        c = build_correction(**FIXTURE_CASES[2])
        self.assertTrue(c["disagreement"]["priority_changed"])
        self.assertTrue(c["disagreement"]["issue_type_changed"])
        self.assertEqual(c["disagreement"]["urgency_divergence"], "UPGRADED_TO_URGENT")


class TestAdjudication(unittest.TestCase):
    def setUp(self):
        self.corrections = load_synthetic_corrections()
        self.policy = {"version": "v3-proposed", "contract": "batch2-classification-v3"}
        self.policy_hash = digest(self.policy)

    def test_adjudicate_affirmed_correction(self):
        c = self.corrections[1]  # Vending machine dining -> IT
        adj = adjudicate_correction(
            c,
            adjudicator_id="adjudicator-audit-01",
            verdict="AFFIRMED_CORRECTION",
            final_label={
                "department": "IT",
                "issue_type": "Vending",
                "priority": "LOW",
                "safety_escalation": False,
            },
            rationale="Vending card payment systems are IT assets per Section 4.2.",
            approval_ref="APPROVAL-REF-01",
            policy_version="v3-proposed",
            policy_sha256=self.policy_hash,
            adjudicated_at="2026-11-06T18:00:00+00:00",
        )
        self.assertEqual(adj["status"], "ADJUDICATED_APPROVED")
        self.assertEqual(adj["final_label"]["department"], "IT")
        self.assertTrue(adj["adjudication_id"].startswith("adj-"))

    def test_adjudicator_cannot_be_same_as_staff_corrector(self):
        c = self.corrections[1]
        staff_id = c["staff_action"]["staff_user_id"]
        with self.assertRaises(GateBlockedError):
            adjudicate_correction(
                c,
                adjudicator_id=staff_id,  # Conflict of interest
                verdict="AFFIRMED_CORRECTION",
                final_label={
                    "department": "IT",
                    "issue_type": "Vending",
                    "priority": "LOW",
                    "safety_escalation": False,
                },
                rationale="Self approval",
                approval_ref="APPROVAL-REF-01",
                policy_version="v3-proposed",
                policy_sha256=self.policy_hash,
                adjudicated_at="2026-11-06T18:00:00+00:00",
            )

    def test_policy_ambiguity_excludes_from_training(self):
        c = self.corrections[5]  # Border tree branch
        adj = adjudicate_correction(
            c,
            adjudicator_id="adjudicator-audit-01",
            verdict="POLICY_AMBIGUITY",
            final_label=None,
            rationale="Jurisdiction over municipal power line border is ambiguous.",
            approval_ref="APPROVAL-REF-01",
            policy_version="v3-proposed",
            policy_sha256=self.policy_hash,
            adjudicated_at="2026-11-06T18:00:00+00:00",
        )
        self.assertEqual(adj["status"], "AMBIGUOUS_EXCLUDED")
        self.assertIsNone(adj["final_label"])

    def test_filter_approved_adjudications_blocks_unapproved_records(self):
        c = self.corrections[0]
        # Raw correction is PENDING_ADJUDICATION
        with self.assertRaises(GateBlockedError):
            filter_approved_adjudications([c], enforce_gate=True)


class TestActiveLearning(unittest.TestCase):
    def test_entropy_and_margin_scoring(self):
        self.assertAlmostEqual(entropy_normalized([0.5, 0.5]), 1.0)
        self.assertAlmostEqual(entropy_normalized([1.0, 0.0]), 0.0)
        self.assertAlmostEqual(margin_score([0.9, 0.1]), 0.2)
        self.assertAlmostEqual(margin_score([0.51, 0.49]), 0.98)

    def test_safety_risk_scoring(self):
        # Report with 'smoke' keyword and staff upgrade to URGENT
        score = safety_risk_score("Kitchenette microwave sparking with visible smoke", "MEDIUM", "URGENT")
        self.assertEqual(score, 1.0)
        # Routine report with no hazard keywords
        score_routine = safety_risk_score("Need extra folding chairs in student lounge", "LOW", "LOW")
        self.assertEqual(score_routine, 0.0)

    def test_active_learning_selection_and_near_duplicate_filter(self):
        corrections = load_synthetic_corrections()
        selector = ActiveLearningSelector(
            weight_uncertainty=0.35,
            weight_disagreement=0.35,
            weight_safety=0.30,
            similarity_threshold=0.85,
        )
        selected = selector.select(corrections, batch_size=3)
        self.assertEqual(len(selected), 3)
        # The urgent electrical hazard case (5003) should be ranked highest due to safety boost
        self.assertEqual(selected[0]["ticket_id"], 5003)


class TestDatasetVersioning(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.corrections = load_synthetic_corrections()
        self.policy = {"version": "v3-proposed", "contract": "batch2-classification-v3"}
        self.policy_hash = digest(self.policy)
        self.adjudicated = []
        for c in self.corrections[:5]:
            adj = adjudicate_correction(
                c,
                adjudicator_id="adjudicator-audit-01",
                verdict="AFFIRMED_CORRECTION",
                final_label={
                    "department": c["staff_action"]["department"],
                    "issue_type": c["staff_action"]["issue_type"],
                    "priority": c["staff_action"]["priority"],
                    "safety_escalation": (c["staff_action"]["priority"] == "URGENT"),
                },
                rationale="Approved policy ground truth.",
                approval_ref="APPROVAL-REF-01",
                policy_version="v3-proposed",
                policy_sha256=self.policy_hash,
                adjudicated_at="2026-11-06T18:00:00+00:00",
            )
            self.adjudicated.append(adj)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_and_verify_dataset_bundle(self):
        bundle_path = self.tmp_dir / "dataset-bundle"
        manifest = create_dataset_bundle(
            bundle_path,
            self.adjudicated,
            self.policy,
            version="v1.0.0",
            seed="seed-1234",
            custodian_ref="CUSTODIAN-01",
            approval_ref="APPROVAL-REF-01",
        )
        self.assertTrue((bundle_path / "manifest.json").exists())
        self.assertTrue((bundle_path / "train.jsonl").exists())
        self.assertTrue((bundle_path / "val.jsonl").exists())
        self.assertTrue((bundle_path / "test.jsonl").exists())

        verified = verify_dataset_bundle(bundle_path)
        self.assertEqual(verified["dataset_id"], manifest["dataset_id"])

    def test_dataset_creation_blocks_unapproved_records(self):
        bundle_path = self.tmp_dir / "blocked-bundle"
        unapproved = list(self.adjudicated) + [self.corrections[5]]  # Raw unadjudicated
        with self.assertRaises(GateBlockedError):
            create_dataset_bundle(
                bundle_path,
                unapproved,
                self.policy,
                version="v1.0.0",
                seed="seed-1234",
                custodian_ref="CUSTODIAN-01",
                approval_ref="APPROVAL-REF-01",
            )

    def test_tampered_adjudication_or_self_rehashed_invalid_verdict_is_rejected(self):
        record=deepcopy(self.adjudicated[0]);record['final_label']['priority']='URGENT'
        with self.assertRaises(ProvenanceError): filter_approved_adjudications([record])
        record['adjudication_sha256']=digest({k:v for k,v in record.items() if k!='adjudication_sha256'})
        with self.assertRaises(AdjudicationError): filter_approved_adjudications([record])

    def test_dataset_cannot_substitute_a_different_policy(self):
        with self.assertRaises(DatasetError):
            create_dataset_bundle(self.tmp_dir/'wrong-policy',self.adjudicated,dict(self.policy,contract='changed'),
                'v1','fixture-seed',custodian_ref='CUST-01',approval_ref='APP-01')

    def test_synthetic_origin_cannot_be_disabled_or_silently_mislabeled(self):
        with self.assertRaises(GateBlockedError):
            create_dataset_bundle(self.tmp_dir/'no-fixtures',self.adjudicated,self.policy,'v1','fixture-seed',
                custodian_ref='CUST-01',approval_ref='APP-01',allow_test_fixtures=False)
        record=deepcopy(self.adjudicated[0]);source=record['correction_snapshot'];source['origin']='real_world'
        source['provenance_sha256']=digest({k:v for k,v in source.items() if k!='provenance_sha256'})
        record['correction_provenance_sha256']=source['provenance_sha256']
        record['adjudication_sha256']=digest({k:v for k,v in record.items() if k!='adjudication_sha256'})
        with self.assertRaises(GateBlockedError):
            create_dataset_bundle(self.tmp_dir/'real-blocked',[record],self.policy,'v1','fixture-seed',
                custodian_ref='CUST-01',approval_ref='APP-01')

    def test_manifest_cannot_read_outside_bundle_even_when_self_rehashed(self):
        bundle=self.tmp_dir/'bundle'
        manifest=create_dataset_bundle(bundle,self.adjudicated,self.policy,'v1','fixture-seed',
            custodian_ref='CUST-01',approval_ref='APP-01')
        manifest['file_hashes']['../canary']='a'*64
        manifest['dataset_id']=digest({k:v for k,v in manifest.items() if k!='dataset_id'})
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(DatasetError): verify_dataset_bundle(bundle)


class TestLoRAAndQLoRA(unittest.TestCase):
    def test_parameter_calculation(self):
        cfg = LoRAConfig(model_id="Qwen/Qwen2.5-7B-Instruct", r=16, quantization="4bit")
        stats = calculate_trainable_parameters(cfg)
        self.assertEqual(stats["model_id"], "Qwen/Qwen2.5-7B-Instruct")
        self.assertLess(stats["trainable_fraction"], 0.01)  # LoRA is < 1% trainable

    def test_peft_adapter_config(self):
        cfg = LoRAConfig(model_id="deepseek-ai/DeepSeek-R1-Distill-Qwen-8B", r=32, quantization="8bit")
        adapter = peft_adapter_config(cfg)
        self.assertEqual(adapter["peft_type"], "LORA")
        self.assertEqual(adapter["r"], 32)
        self.assertEqual(adapter["quantization"], "8bit")

    def test_offline_lora_simulation(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

        # Setup dataset bundle
        corrections = load_synthetic_corrections()
        policy = {"version": "v3-proposed", "contract": "batch2-classification-v3"}
        adjudicated = [
            adjudicate_correction(
                c,
                adjudicator_id="adjudicator-audit-01",
                verdict="AFFIRMED_CORRECTION",
                final_label={
                    "department": c["staff_action"]["department"],
                    "issue_type": c["staff_action"]["issue_type"],
                    "priority": c["staff_action"]["priority"],
                    "safety_escalation": (c["staff_action"]["priority"] == "URGENT"),
                },
                rationale="Approved.",
                approval_ref="APP-01",
                policy_version="v3-proposed",
                policy_sha256=digest(policy),
                adjudicated_at="2026-11-06T18:00:00+00:00",
            )
            for c in corrections[:5]
        ]
        bundle = tmp_dir / "bundle"
        create_dataset_bundle(bundle, adjudicated, policy, "v1", "seed1", custodian_ref="CUST-01", approval_ref="APP-01")

        sim_out = tmp_dir / "lora_sim"
        cfg = LoRAConfig(model_id="Qwen/Qwen2.5-1.5B-Instruct", r=8, epochs=2)
        receipt = simulate_lora_training(bundle, cfg, sim_out, approval_ref="APP-01")

        self.assertEqual(receipt["status"], "SIMULATED_TRAINING_COMPLETE")
        self.assertTrue((sim_out / "adapter_config.json").exists())
        self.assertTrue((sim_out / "experiment_receipt.json").exists())
        self.assertLess(receipt["final_simulated_loss"], 2.45)


class TestDistillation(unittest.TestCase):
    def test_distillation_loss_and_kl_divergence(self):
        cfg = DistillationConfig(temperature=2.0, alpha_kd=0.6)
        teacher_logits = [2.0, 1.0, 0.1, -0.5]
        student_logits = [1.8, 1.2, 0.0, -0.4]
        loss_dict = combined_distillation_loss(teacher_logits, student_logits, target_idx=0, config=cfg)
        self.assertIn("total_loss", loss_dict)
        self.assertGreater(loss_dict["total_loss"], 0.0)
        self.assertGreater(loss_dict["scaled_kl_loss"], 0.0)

    def test_softmax_temperature_smoothing(self):
        logits = [5.0, 1.0, -2.0]
        sharp = softmax_with_temperature(logits, tau=1.0)
        smooth = softmax_with_temperature(logits, tau=5.0)
        self.assertGreater(sharp[0], smooth[0])  # Higher temperature produces smoother distribution


class TestFormatters(unittest.TestCase):
    def test_format_chatml_and_prompt_completion(self):
        label = {"department": "Facilities", "issue_type": "Plumbing", "priority": "HIGH"}
        chatml = format_chatml("Leaking pipe", label)
        self.assertIn("<|im_start|>system", chatml)
        self.assertIn('"department":"Facilities"', chatml)

        pc = format_prompt_and_completion("Leaking pipe", label)
        self.assertIn("Report:\nLeaking pipe", pc["prompt"])
        self.assertEqual(json.loads(pc["completion"]), label)


if __name__ == "__main__":
    unittest.main()

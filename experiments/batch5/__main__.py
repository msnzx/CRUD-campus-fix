"""CLI entry point for Batch 5 research, provenance, active learning, and experiments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .active_learning import ActiveLearningSelector
from .adjudication import adjudicate_correction
from .common import digest, write_json
from .correction import build_correction
from .dataset import create_dataset_bundle
from .distillation import DistillationConfig, simulate_distillation
from .fixtures import load_synthetic_corrections
from .lora import LoRAConfig, calculate_trainable_parameters, peft_adapter_config, simulate_lora_training


def subcommand_provenance(args: argparse.Namespace) -> int:
    corrections = load_synthetic_corrections()
    write_json(args.out, {
        "status": "PROVENANCE_GENERATED",
        "count": len(corrections),
        "records": corrections,
    })
    print(f"Generated {len(corrections)} synthetic correction provenance records -> {args.out}")
    return 0


def subcommand_adjudicate(args: argparse.Namespace) -> int:
    corrections = load_synthetic_corrections()
    adjudicated = []
    policy_hash = digest({"version": "v3-proposed", "contract": "batch2-classification-v3"})

    verdict_map = {
        5001: ("AFFIRMED_CORRECTION", {"department": "Facilities", "issue_type": "Furniture", "priority": "LOW", "safety_escalation": False}),
        5002: ("AFFIRMED_CORRECTION", {"department": "IT", "issue_type": "Vending", "priority": "LOW", "safety_escalation": False}),
        5003: ("AFFIRMED_CORRECTION", {"department": "Facilities", "issue_type": "Electrical", "priority": "URGENT", "safety_escalation": True}),
        5004: ("AFFIRMED_CORRECTION", {"department": "Facilities", "issue_type": "Structural", "priority": "HIGH", "safety_escalation": False}),
        5005: ("AFFIRMED_AI", {"department": "IT", "issue_type": "Classroom Technology", "priority": "MEDIUM", "safety_escalation": False}),
        5006: ("POLICY_AMBIGUITY", None),
    }

    for c in corrections:
        tid = c["ticket_id"]
        verdict, final_label = verdict_map.get(tid, ("POLICY_AMBIGUITY", None))
        adj = adjudicate_correction(
            c,
            adjudicator_id="adjudicator-audit-01",
            verdict=verdict,
            final_label=final_label,
            rationale=f"Independent review of ticket {tid} per campus classification guidelines.",
            approval_ref="POLICY-APPROVAL-2026-B5",
            policy_version="v3-proposed",
            policy_sha256=policy_hash,
            adjudicated_at="2026-11-06T18:00:00+00:00",
        )
        adjudicated.append(adj)

    write_json(args.out, {
        "status": "ADJUDICATED",
        "count": len(adjudicated),
        "approved_count": sum(1 for a in adjudicated if a["status"] == "ADJUDICATED_APPROVED"),
        "records": adjudicated,
    })
    print(f"Adjudicated {len(adjudicated)} corrections -> {args.out}")
    return 0


def subcommand_active_learning(args: argparse.Namespace) -> int:
    corrections = load_synthetic_corrections()
    selector = ActiveLearningSelector()
    selected = selector.select(corrections, batch_size=args.batch_size)
    result = {
        "status": "ACTIVE_LEARNING_SELECTED",
        "requested_batch_size": args.batch_size,
        "selected_count": len(selected),
        "selected": selected,
    }
    write_json(args.out, result)
    print(f"Active learning selected {len(selected)} priority cases -> {args.out}")
    return 0


def subcommand_dataset(args: argparse.Namespace) -> int:
    corrections = load_synthetic_corrections()
    policy = {"version": "v3-proposed", "contract": "batch2-classification-v3"}
    policy_hash = digest(policy)
    adjudicated = []
    for c in corrections[:5]:  # Take the first 5 unambiguous cases
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
            rationale="Affirmed in accordance with approved policy guidelines.",
            approval_ref="DATASET-APPROVAL-2026-B5",
            policy_version="v3-proposed",
            policy_sha256=policy_hash,
            adjudicated_at="2026-11-06T18:00:00+00:00",
        )
        adjudicated.append(adj)

    manifest = create_dataset_bundle(
        out_dir=args.out,
        records=adjudicated,
        policy=policy,
        version=args.version,
        seed="batch5-seed-2026",
        custodian_ref="CUSTODIAN-DATA-OFFICER-01",
        approval_ref="DATASET-APPROVAL-2026-B5",
    )
    print(f"Created dataset bundle {manifest['dataset_version']} ({manifest['total_count']} cases) -> {args.out}")
    return 0


def subcommand_lora_plan(args: argparse.Namespace) -> int:
    config = LoRAConfig(model_id=args.model_id, r=args.rank, quantization=args.quantization)
    stats = calculate_trainable_parameters(config)
    adapter_cfg = peft_adapter_config(config)
    plan = {
        "status": "LORA_PLAN_READY",
        "config": json.loads(json.dumps(config.__dict__)),
        "parameter_statistics": stats,
        "peft_adapter_config": adapter_cfg,
    }
    write_json(args.out, plan)
    print(f"Generated LoRA plan ({stats['trainable_percentage']} trainable) -> {args.out}")
    return 0


def subcommand_distill_plan(args: argparse.Namespace) -> int:
    config = DistillationConfig(teacher_model=args.teacher, student_model=args.student, temperature=args.temperature)
    plan = {
        "status": "DISTILLATION_PLAN_READY",
        "config": json.loads(json.dumps(config.__dict__)),
    }
    write_json(args.out, plan)
    print(f"Generated distillation plan ({config.teacher_model} -> {config.student_model}) -> {args.out}")
    return 0


def subcommand_simulate(args: argparse.Namespace) -> int:
    lora_cfg = LoRAConfig(model_id=args.model_id, r=args.rank, epochs=args.epochs)
    dist_cfg = DistillationConfig(epochs=args.epochs)
    lora_out = args.out / "lora_adapter"
    dist_out = args.out / "distillation"
    lora_receipt = simulate_lora_training(args.dataset, lora_cfg, lora_out, approval_ref=args.approval_ref)
    dist_receipt = simulate_distillation(args.dataset, dist_cfg, dist_out, approval_ref=args.approval_ref)
    summary = {
        "status": "SIMULATION_EXPERIMENTS_COMPLETE",
        "lora_final_loss": lora_receipt["final_simulated_loss"],
        "distill_final_loss": dist_receipt["final_loss"],
        "artifacts": {
            "lora_adapter": str(lora_out),
            "distillation": str(dist_out),
        },
        "approval_ref": args.approval_ref,
    }
    write_json(args.out / "simulation_summary.json", summary)
    print(f"Executed offline simulations (LoRA loss: {summary['lora_final_loss']}, Distill loss: {summary['distill_final_loss']}) -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Provenance
    p_prov = subparsers.add_parser("provenance")
    p_prov.add_argument("--out", type=Path, required=True)
    p_prov.set_defaults(func=subcommand_provenance)

    # Adjudication
    p_adj = subparsers.add_parser("adjudicate")
    p_adj.add_argument("--out", type=Path, required=True)
    p_adj.set_defaults(func=subcommand_adjudicate)

    # Active learning
    p_al = subparsers.add_parser("active-learning")
    p_al.add_argument("--out", type=Path, required=True)
    p_al.add_argument("--batch-size", type=int, default=3)
    p_al.set_defaults(func=subcommand_active_learning)

    # Dataset
    p_ds = subparsers.add_parser("dataset")
    p_ds.add_argument("--out", type=Path, required=True)
    p_ds.add_argument("--version", type=str, default="batch5-v1.0.0")
    p_ds.set_defaults(func=subcommand_dataset)

    # LoRA plan
    p_lora = subparsers.add_parser("lora-plan")
    p_lora.add_argument("--out", type=Path, required=True)
    p_lora.add_argument("--model-id", type=str, default="Qwen/Qwen2.5-7B-Instruct")
    p_lora.add_argument("--rank", type=int, default=16)
    p_lora.add_argument("--quantization", type=str, default="4bit")
    p_lora.set_defaults(func=subcommand_lora_plan)

    # Distill plan
    p_dist = subparsers.add_parser("distill-plan")
    p_dist.add_argument("--out", type=Path, required=True)
    p_dist.add_argument("--teacher", type=str, default="deepseek-r1:8b")
    p_dist.add_argument("--student", type=str, default="Qwen/Qwen2.5-1.5B-Instruct")
    p_dist.add_argument("--temperature", type=float, default=2.0)
    p_dist.set_defaults(func=subcommand_distill_plan)

    # Simulate
    p_sim = subparsers.add_parser("simulate")
    p_sim.add_argument("--dataset", type=Path, required=True)
    p_sim.add_argument("--out", type=Path, required=True)
    p_sim.add_argument("--model-id", type=str, default="Qwen/Qwen2.5-1.5B-Instruct")
    p_sim.add_argument("--rank", type=int, default=8)
    p_sim.add_argument("--epochs", type=int, default=2)
    p_sim.add_argument("--approval-ref", type=str, default="APP-SIM-2026")
    p_sim.set_defaults(func=subcommand_simulate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

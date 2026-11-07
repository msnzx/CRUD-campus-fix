"""Reproducible knowledge distillation experiment framework.

Implements declarative teacher-student distillation configurations, soft-target
temperature scaling, Kullback-Leibler divergence combined with task cross-entropy,
and deterministic offline simulation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any

from .common import (
    DEPARTMENTS, ExperimentError, GateBlockedError, canonical, digest, require,
    token, write_json
)
from .dataset import verify_dataset_bundle


@dataclass(frozen=True)
class DistillationConfig:
    teacher_model: str = "deepseek-r1:8b"
    student_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    temperature: float = 2.0
    alpha_kd: float = 0.60
    learning_rate: float = 3e-4
    epochs: int = 3
    batch_size: int = 4
    seed: int = 42

    def validate(self) -> None:
        if not self.teacher_model.strip():
            raise ExperimentError("Teacher model cannot be blank")
        if not self.student_model.strip():
            raise ExperimentError("Student model cannot be blank")
        if self.teacher_model == self.student_model:
            raise ExperimentError("Teacher and student models must be distinct")
        if not (1.0 <= self.temperature <= 10.0):
            raise ExperimentError("Distillation temperature must be in [1.0, 10.0]")
        if not (0.0 <= self.alpha_kd <= 1.0):
            raise ExperimentError("alpha_kd must be in [0.0, 1.0]")
        if not (1e-6 <= self.learning_rate <= 1e-2):
            raise ExperimentError("learning_rate must be in [1e-6, 1e-2]")
        if self.epochs < 1 or self.batch_size < 1:
            raise ExperimentError("epochs and batch_size must be positive integers")


def softmax_with_temperature(logits: list[float], tau: float) -> list[float]:
    """Compute softmax with temperature scaling."""
    scaled = [x / tau for x in logits]
    max_val = max(scaled)
    exp_vals = [math.exp(x - max_val) for x in scaled]
    total = sum(exp_vals)
    return [x / total for x in exp_vals]


def kl_divergence(p: list[float], q: list[float]) -> float:
    """Compute KL divergence D_KL(P || Q) = sum(P_i * log(P_i / Q_i))."""
    div = 0.0
    for p_i, q_i in zip(p, q):
        if p_i > 0:
            q_safe = max(1e-12, q_i)
            div += p_i * math.log(p_i / q_safe)
    return max(0.0, div)


def combined_distillation_loss(
    teacher_logits: list[float],
    student_logits: list[float],
    target_idx: int,
    config: DistillationConfig,
) -> dict[str, float]:
    """Compute distillation loss: alpha * tau^2 * KL + (1 - alpha) * CE."""
    tau = config.temperature
    alpha = config.alpha_kd

    # 1. Soft target distributions
    p_teacher = softmax_with_temperature(teacher_logits, tau)
    p_student = softmax_with_temperature(student_logits, tau)

    # 2. KL Divergence (Student relative to Teacher)
    l_kl = kl_divergence(p_student, p_teacher)
    scaled_kl = (tau * tau) * l_kl

    # 3. Hard label cross-entropy
    p_std_hard = softmax_with_temperature(student_logits, 1.0)
    target_p = max(1e-12, p_std_hard[target_idx])
    l_ce = -math.log(target_p)

    total_loss = (alpha * scaled_kl) + ((1.0 - alpha) * l_ce)
    return {
        "total_loss": total_loss,
        "kl_loss": l_kl,
        "scaled_kl_loss": scaled_kl,
        "ce_loss": l_ce,
    }


def simulate_distillation(
    dataset_bundle_dir: Path,
    config: DistillationConfig,
    out_dir: Path,
    *,
    approval_ref: str,
) -> dict[str, Any]:
    """Execute deterministic offline student distillation experiment."""
    config.validate()
    if not token(approval_ref):
        raise GateBlockedError("Approved approval_ref required for distillation execution")

    manifest = verify_dataset_bundle(dataset_bundle_dir)
    train_count = manifest["counts"].get("train", 0)
    if train_count == 0:
        raise ExperimentError("Cannot distill on empty train split")

    total_steps = max(1, math.ceil(train_count / config.batch_size)) * config.epochs
    history = []
    current_loss = 2.100

    for step in range(1, total_steps + 1):
        step_hash = int(digest([config.seed, step])[:6], 16) % 100
        delta = (0.012 * (1.0 / (1.0 + 0.04 * step))) + (0.0008 * (step_hash / 100.0))
        current_loss = max(0.200, current_loss - delta)
        if step % max(1, total_steps // 5) == 0 or step == total_steps:
            history.append({
                "step": step,
                "loss": round(current_loss, 4),
            })

    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "status": "SIMULATED_DISTILLATION_COMPLETE",
        "teacher_model": config.teacher_model,
        "student_model": config.student_model,
        "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"],
        "config": asdict(config),
        "total_steps": total_steps,
        "final_loss": history[-1]["loss"],
        "history": history,
        "approval_ref": approval_ref,
        "offline_simulation": True,
        "paid_calls_made": 0,
    }
    receipt["experiment_sha256"] = digest({k: v for k, v in receipt.items() if k != "experiment_sha256"})
    write_json(out_dir / "distillation_receipt.json", receipt)

    return receipt

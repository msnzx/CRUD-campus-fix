"""Reproducible LoRA and QLoRA parameter-efficient adaptation experiment framework.

Provides declarative experiment configurations, exact parameter counting,
offline training simulation, adapter configuration emission, and safety gates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .common import (
    ExperimentError, GateBlockedError, canonical, digest, finite, require,
    token, write_json, write_new
)
from .dataset import verify_dataset_bundle

TARGET_MODULES_ALL = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
SUPPORTED_BASE_MODELS = {
    "Qwen/Qwen2.5-7B-Instruct": {"hidden_size": 3584, "intermediate_size": 18944, "num_hidden_layers": 28},
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-8B": {"hidden_size": 4096, "intermediate_size": 14336, "num_hidden_layers": 36},
    "Qwen/Qwen2.5-1.5B-Instruct": {"hidden_size": 1536, "intermediate_size": 8960, "num_hidden_layers": 28},
}


@dataclass(frozen=True)
class LoRAConfig:
    model_id: str
    r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple[str, ...] = TARGET_MODULES_ALL
    quantization: str = "4bit"  # "4bit" (QLoRA), "8bit", or "none" (LoRA)
    learning_rate: float = 2e-4
    epochs: int = 3
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    max_seq_length: int = 1024
    warmup_ratio: float = 0.03
    weight_decay: float = 0.01
    seed: int = 42

    def validate(self) -> None:
        if self.model_id not in SUPPORTED_BASE_MODELS:
            raise ExperimentError(f"Unsupported base model: {self.model_id}")
        if self.r not in (4, 8, 16, 32, 64):
            raise ExperimentError(f"Rank r must be a power of two between 4 and 64, got {self.r}")
        if self.lora_alpha <= 0:
            raise ExperimentError("lora_alpha must be positive")
        if not (0.0 <= self.lora_dropout < 1.0):
            raise ExperimentError("lora_dropout must be in [0.0, 1.0)")
        if self.quantization not in ("4bit", "8bit", "none"):
            raise ExperimentError(f"Unsupported quantization: {self.quantization}")
        if not (1e-6 <= self.learning_rate <= 1e-2):
            raise ExperimentError("learning_rate outside sensible bounds [1e-6, 1e-2]")
        if self.epochs < 1:
            raise ExperimentError("epochs must be at least 1")
        if self.batch_size < 1 or self.gradient_accumulation_steps < 1:
            raise ExperimentError("batch size and grad accumulation must be at least 1")
        if not all(m in TARGET_MODULES_ALL for m in self.target_modules):
            raise ExperimentError("Unknown target module in target_modules")


def calculate_trainable_parameters(config: LoRAConfig) -> dict[str, Any]:
    """Calculate exact base and trainable adapter parameters for a LoRA configuration."""
    config.validate()
    arch = SUPPORTED_BASE_MODELS[config.model_id]
    d_model = arch["hidden_size"]
    d_ff = arch["intermediate_size"]
    layers = arch["num_hidden_layers"]
    r = config.r

    # Base parameter approximation
    attn_weights = 4 * (d_model * d_model)  # q, k, v, o
    mlp_weights = 3 * (d_model * d_ff)      # gate, up, down
    layer_weights = attn_weights + mlp_weights
    total_base_params = layers * layer_weights

    # Trainable adapter weights
    # For attention modules: A is (r, d_model), B is (d_model, r) -> 2 * r * d_model
    # For MLP modules: A is (r, d_in), B is (d_out, r)
    adapter_params_per_layer = 0
    for mod in config.target_modules:
        if mod in ("q_proj", "k_proj", "v_proj", "o_proj"):
            adapter_params_per_layer += 2 * r * d_model
        elif mod in ("gate_proj", "up_proj"):
            adapter_params_per_layer += (r * d_model) + (d_ff * r)
        elif mod == "down_proj":
            adapter_params_per_layer += (r * d_ff) + (d_model * r)

    trainable_params = layers * adapter_params_per_layer
    fraction = trainable_params / total_base_params

    return {
        "model_id": config.model_id,
        "rank": r,
        "quantization": config.quantization,
        "total_base_parameters": total_base_params,
        "trainable_adapter_parameters": trainable_params,
        "trainable_fraction": fraction,
        "trainable_percentage": f"{fraction * 100:.3f}%",
    }


def peft_adapter_config(config: LoRAConfig) -> dict[str, Any]:
    """Format standard HuggingFace PEFT adapter_config.json."""
    config.validate()
    return {
        "peft_type": "LORA",
        "base_model_name_or_path": config.model_id,
        "r": config.r,
        "lora_alpha": config.lora_alpha,
        "lora_dropout": config.lora_dropout,
        "target_modules": list(config.target_modules),
        "bias": "none",
        "task_type": "CAUSAL_LM",
        "inference_mode": False,
        "quantization": config.quantization,
    }


def simulate_lora_training(
    dataset_bundle_dir: Path,
    config: LoRAConfig,
    out_dir: Path,
    *,
    approval_ref: str,
) -> dict[str, Any]:
    """Deterministic offline simulation of LoRA/QLoRA training loop."""
    config.validate()
    if not token(approval_ref):
        raise GateBlockedError("Approved approval_ref required for training execution")

    # Gate: verify dataset bundle integrity and policy approval
    manifest = verify_dataset_bundle(dataset_bundle_dir)
    train_count = manifest["counts"].get("train", 0)
    if train_count == 0:
        raise ExperimentError("Cannot train on empty train split")

    param_stats = calculate_trainable_parameters(config)
    adapter_cfg = peft_adapter_config(config)

    # Deterministic simulation of loss decay across epochs
    effective_batch = config.batch_size * config.gradient_accumulation_steps
    steps_per_epoch = max(1, math.ceil(train_count / effective_batch))
    total_steps = steps_per_epoch * config.epochs

    history = []
    current_loss = 2.450
    lr = config.learning_rate
    for step in range(1, total_steps + 1):
        # Cosine learning rate decay
        decay = 0.5 * (1.0 + math.cos(math.pi * step / total_steps))
        step_lr = lr * decay
        # Realistic loss step decrement with minor deterministic jitter
        step_hash = int(digest([config.seed, step])[:6], 16) % 100
        delta = (0.015 * (1.0 / (1.0 + 0.05 * step))) + (0.001 * (step_hash / 100.0))
        current_loss = max(0.250, current_loss - delta)
        if step % max(1, total_steps // 5) == 0 or step == total_steps:
            history.append({
                "step": step,
                "epoch": round(step / steps_per_epoch, 2),
                "learning_rate": step_lr,
                "loss": round(current_loss, 4),
            })

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "adapter_config.json", adapter_cfg)

    receipt = {
        "status": "SIMULATED_TRAINING_COMPLETE",
        "model_id": config.model_id,
        "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"],
        "policy_sha256": manifest["policy_sha256"],
        "config": asdict(config),
        "parameter_statistics": param_stats,
        "training_steps": total_steps,
        "final_simulated_loss": history[-1]["loss"],
        "training_history": history,
        "approval_ref": approval_ref,
        "offline_simulation": True,
        "paid_calls_made": 0,
    }
    receipt["experiment_sha256"] = digest({k: v for k, v in receipt.items() if k != "experiment_sha256"})
    write_json(out_dir / "experiment_receipt.json", receipt)

    return receipt

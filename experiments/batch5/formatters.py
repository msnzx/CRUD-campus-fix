"""Prompt and instruction formatters for fine-tuning and student distillation."""
from __future__ import annotations

import json
from typing import Any

from .common import Batch5Error, require

SYSTEM_INSTRUCTION = (
    "You are the CampusFix ticket classification assistant. "
    "Analyze the campus maintenance or facilities report and assign the responsible department, "
    "issue type, and priority. Respond ONLY with a valid JSON object."
)


def format_target_json(label: dict[str, Any]) -> str:
    """Format label as standard canonical JSON completion string."""
    obj = {
        "department": label["department"],
        "issue_type": label["issue_type"],
        "priority": label["priority"],
    }
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def format_chatml(report_text: str, label: dict[str, Any]) -> str:
    """Render sample in standard ChatML instruction format."""
    target_str = format_target_json(label)
    return (
        f"<|im_start|>system\n{SYSTEM_INSTRUCTION}<|im_end|>\n"
        f"<|im_start|>user\n{report_text.strip()}<|im_end|>\n"
        f"<|im_start|>assistant\n{target_str}<|im_end|>"
    )


def format_prompt_and_completion(report_text: str, label: dict[str, Any]) -> dict[str, str]:
    """Render prompt and target completion for supervised fine-tuning."""
    prompt = (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"Report:\n{report_text.strip()}\n\n"
        f"Classification JSON:"
    )
    return {
        "prompt": prompt,
        "completion": format_target_json(label),
    }


def format_dataset_split(
    records: list[dict[str, Any]],
    formatter_name: str = "chatml"
) -> list[dict[str, Any]]:
    """Convert raw dataset records into formatted instruction tuning instances."""
    results = []
    for r in records:
        text = r.get("text") or r.get("report_text")
        label = r.get("label") or r.get("final_label")
        if not text or not label:
            raise Batch5Error("Cannot format record: missing text or label")
        if formatter_name == "chatml":
            results.append({
                "case_id": r.get("case_id"),
                "formatted_text": format_chatml(text, label),
            })
        elif formatter_name == "prompt_response":
            formatted = format_prompt_and_completion(text, label)
            results.append({
                "case_id": r.get("case_id"),
                **formatted,
            })
        else:
            raise Batch5Error(f"Unknown formatter: {formatter_name}")
    return results

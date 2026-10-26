"""Read-only live smoke check: Supabase taxonomy -> DeepSeek -> routing policy.

Run from backend with a configured .env and Ollama running:
    python smoke_routing.py

Only taxonomy SELECTs and local inference are performed; no tickets are written.
API persistence and authorization are covered by tests/test_routing.py.
"""

from time import perf_counter

from app.auth import admin_client
from app.classify import _reference_data, classify
from app.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.ai_enabled:
        raise SystemExit("Configure the Supabase service role and enable AI classification first.")

    admin = admin_client(settings)
    ref = _reference_data(admin)
    if not all(ref.values()):
        raise SystemExit("Required taxonomy is empty.")
    print("Live taxonomy:", ", ".join(f"{len(rows)} {name}" for name, rows in ref.items()), flush=True)
    issues = {r["issue_type_id"]: r["name"] for r in ref["issue_types"]}
    departments = {r["department_id"]: r["name"] for r in ref["departments"]}
    priorities = {r["priority_id"]: r["name"] for r in ref["priorities"]}
    # Small, fixed examples rather than touching the benchmark holdout.
    cases = [
        "The bathroom sink faucet drips into the basin continuously.",
        "The classroom projector will not display the instructor's slides.",
        "My meal plan has the wrong swipe balance in the dining account.",
        "I need help submitting FAFSA financial aid eligibility documents.",
        "The vending machine took my payment without dispensing the snack.",
        "I am locked out of my dorm room after losing my room key.",
        "The wifi keeps failing; also the sink pipe is leaking.",
        "wifi broken",
    ]
    failures = 0
    for index, text in enumerate(cases, 1):
        start = perf_counter()
        result = classify({"title": "", "description": text}, admin, settings)
        if result is None:
            print(f"{index}: FAILED ({perf_counter() - start:.2f}s)", flush=True)
            failures += 1
            continue
        actual = (
            issues.get(result.issue_type_id),
            departments.get(result.department_id),
            priorities.get(result.priority_id),
        )
        # This checks the live integration contract, not model accuracy or the
        # old priority rules. Final accuracy comes from the frozen holdout.
        passed = (
            result.issue_type_id in issues
            and result.department_id in departments
            and result.priority_id in priorities
            and result.confidence == (0.0 if result.needs_review else 1.0)
        )
        if result.needs_review:
            passed = passed and actual == ("Other", "Other / Review", "MEDIUM")
        failures += not passed
        print(
            f"{index}: {'PASS' if passed else 'FAIL'} {actual} "
            f"needs_review={result.needs_review} ({perf_counter() - start:.2f}s)",
            flush=True,
        )
    if failures:
        raise SystemExit(f"{failures} live smoke case(s) failed.")
    print(f"All {len(cases)} live smoke cases passed. No database writes performed.")


if __name__ == "__main__":
    main()

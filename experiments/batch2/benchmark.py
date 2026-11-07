"""Offline imported-prediction metrics, isotonic calibration and abstention.

No model is called and no model can be promoted by this module. The production
route flag is explicitly ineligible as a calibration score.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path

from evaluation.reproduce_metrics import label_metrics
from evaluation.dev_audit.analyze_development import ISSUE_TYPES, DEPARTMENTS, ORDINAL
from .common import digest, file_hash, finite, read_jsonl, require, token, verify_bundle
from .policy import validate_label

STATUS = {"ok", "error", "invalid"}
SCORE_KINDS = {"model_probability", "uncalibrated_score", "route_flag", "unavailable"}
RUN_FIELDS = {"candidate_id", "model_name", "model_digest", "prompt_sha256", "prompt_version",
              "adapter_version", "code_sha256", "decoding", "score_kind", "environment", "partition_sha256"}


def validate_run(run: dict) -> None:
    require(set(run) == RUN_FIELDS, "Run provenance fields are incomplete")
    for name in ("candidate_id", "model_name", "prompt_version", "adapter_version"):
        require(token(run[name]), "Invalid candidate provenance reference")
    for name in ("model_digest", "prompt_sha256", "code_sha256", "partition_sha256"):
        require(isinstance(run[name], str) and len(run[name]) == 64 and all(c in "0123456789abcdef" for c in run[name]),
                "Immutable run digests are required")
    require(run["score_kind"] in SCORE_KINDS, "Confidence score source must be declared")
    require(isinstance(run["decoding"], dict) and set(run["decoding"]) == {"temperature", "think", "stream", "seed"}
            and finite(run["decoding"]["temperature"]) and run["decoding"]["temperature"] >= 0
            and type(run["decoding"]["think"]) is bool and type(run["decoding"]["stream"]) is bool
            and (run['decoding']['seed'] is None or type(run['decoding']['seed']) is int),
            "Decoding configuration is incomplete")
    require(isinstance(run["environment"], dict) and set(run["environment"]) == {"python", "ollama_version", "hardware"}
            and all(isinstance(v, str) and v.strip() for v in run["environment"].values()), "Runtime environment must be recorded")


def candidate_fingerprint(run: dict) -> str:
    validate_run(run)
    return digest({k: v for k, v in run.items() if k not in ("partition_sha256", "environment")})


def load_work(bundle: Path, split: str) -> tuple[dict, list[dict]]:
    require(split in ("development", "calibration", "validation"), "Independent test data is unavailable to development commands")
    manifest = verify_bundle(bundle)
    rows = read_jsonl(bundle / f"work/{split}.jsonl")
    require(len(rows) == manifest["counts"][split], "Dataset partition count mismatch")
    for row in rows:
        validate_label(row["label"])
    return manifest, rows


def validate_predictions(cases: list[dict], predictions: list[dict], run: dict, partition_hash: str) -> list[dict]:
    validate_run(run)
    require(run["partition_sha256"] == partition_hash, "Predictions belong to a different data revision")
    ids = [p.get("case_id") for p in predictions]
    require(len(ids) == len(set(ids)) and set(ids) == {c["case_id"] for c in cases}, "Predictions must cover each case exactly once")
    fields = {"case_id", "status", "issue_type", "department", "priority", "action", "safety_signal",
              "confidence_score", "latency_seconds", "error_kind"}
    for p in predictions:
        require(set(p) == fields and p["status"] in STATUS, "Invalid prediction contract")
        require(type(p["safety_signal"]) is bool and finite(p["latency_seconds"]) and p["latency_seconds"] >= 0,
                "Invalid safety flag or latency")
        require(p["confidence_score"] is None or (finite(p["confidence_score"]) and 0 <= p["confidence_score"] <= 1),
                "Confidence must be finite and within [0,1]")
        if p["status"] != "ok":
            require(p["action"] == "NEEDS_REVIEW" and p["safety_signal"] is False
                    and all(p[k] is None for k in ("issue_type", "department", "priority", "confidence_score"))
                    and token(p["error_kind"]), "Invalid outputs must be explicitly failed, not silently omitted")
        else:
            require(p["issue_type"] in ISSUE_TYPES and (p["department"] is None or p["department"] in DEPARTMENTS)
                    and (p["priority"] is None or p["priority"] in ORDINAL) and p["error_kind"] is None,
                    "Unknown prediction labels")
            require(p["action"] in ("AUTO_ROUTE", "NEEDS_REVIEW") and p["safety_signal"] == (p["priority"] == "URGENT"),
                    "Safety signal must survive routing abstention")
            require(p["action"] != "AUTO_ROUTE" or (p["issue_type"] != "Other" and p["department"] is not None and p["priority"] is not None),
                    "Incomplete/Other predictions cannot auto-route")
    by_id = {p["case_id"]: p for p in predictions}
    return [by_id[c["case_id"]] for c in cases]


def correct(label: dict, prediction: dict) -> bool:
    return (prediction["status"] == "ok" and label["action"] == "AUTO_ROUTE"
            and all(label[k] == prediction[k] for k in ("issue_type", "department", "priority")))


def wilson(success: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.959963984540054
    p, denominator = success / total, 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1-p) / total + z*z/(4*total*total)) / denominator
    return [max(0.0, centre-radius), min(1.0, centre+radius)]


def calibration_metrics(scores: list[float], outcomes: list[int], bins=10) -> dict:
    require(len(scores) == len(outcomes) and bool(scores) and isinstance(bins, int) and bins > 0, "Calibration metrics need paired observations")
    require(all(finite(s) and 0 <= s <= 1 for s in scores) and all(type(y) is int and y in (0,1) for y in outcomes), "Invalid calibration observations")
    buckets = [[] for _ in range(bins)]
    for score, outcome in zip(scores, outcomes):
        buckets[min(int(score*bins),bins-1)].append((score,outcome))
    reliability, ece = [], 0.0
    for bucket in buckets:
        if bucket:
            confidence = sum(s for s,_ in bucket)/len(bucket)
            accuracy = sum(y for _,y in bucket)/len(bucket)
            ece += len(bucket)/len(scores)*abs(confidence-accuracy)
            reliability.append({"n":len(bucket),"mean_confidence":confidence,"accuracy":accuracy})
    return {"n":len(scores),"brier":sum((s-y)**2 for s,y in zip(scores,outcomes))/len(scores),"ece":ece,"reliability":reliability}


def fit_calibrator(bundle: Path, predictions: list[dict], run: dict) -> dict:
    manifest,cases = load_work(bundle,"calibration")
    ordered = validate_predictions(cases,predictions,run,manifest["files"]["work/calibration.jsonl"])
    require(run["score_kind"] in ("model_probability","uncalibrated_score"), "Route flags/unavailable confidence cannot be calibrated")
    require(all(p["status"] != "ok" or p["confidence_score"] is not None for p in ordered), "Missing scores cannot be fabricated")
    observations = [(p["confidence_score"],int(correct(c["label"],p))) for c,p in zip(cases,ordered) if p["status"]=="ok"]
    require(len(observations)>=2 and len({y for _,y in observations})==2 and len({x for x,_ in observations})>=2
            and any(0 < x < 1 for x,_ in observations), "Calibration needs varied measured scores and both correct/incorrect outcomes")
    # Pool adjacent violators, aggregating ties before monotonic pooling.
    groups = {}
    for score,outcome in observations:
        n,total = groups.get(score,(0,0))
        groups[score] = (n+1,total+outcome)
    blocks=[]
    for score,(n,total) in sorted(groups.items()):
        blocks.append([score,score,n,total])
        while len(blocks)>1 and blocks[-2][3]/blocks[-2][2] > blocks[-1][3]/blocks[-1][2]:
            right,left = blocks.pop(),blocks.pop()
            blocks.append([left[0],right[1],left[2]+right[2],left[3]+right[3]])
    artifact = {"method":"isotonic-pav-v1","target":"joint_auto_route_correctness","status":"RESEARCH_ONLY",
                "dataset_id":manifest["dataset_id"],"policy_sha256":manifest["policy_sha256"],
                "candidate_fingerprint":candidate_fingerprint(run),"fit_split":"calibration",
                "predictions_sha256":digest(predictions),"n":len(observations),"blocks":blocks}
    artifact["artifact_id"] = digest(artifact)
    return artifact


def calibrated_score(artifact: dict, score: float, run: dict, manifest: dict) -> float:
    require(artifact.get("artifact_id")==digest({k:v for k,v in artifact.items() if k!="artifact_id"}), "Calibration artifact checksum mismatch")
    require(artifact["candidate_fingerprint"]==candidate_fingerprint(run) and artifact["dataset_id"]==manifest["dataset_id"]
            and artifact["policy_sha256"]==manifest["policy_sha256"] and artifact["fit_split"]=="calibration", "Calibration scope mismatch")
    require(artifact['method']=='isotonic-pav-v1' and artifact['target']=='joint_auto_route_correctness', "Unsupported calibration contract")
    require(finite(score) and 0 <= score <= 1 and artifact["blocks"], "Invalid score or calibrator")
    prior_high,prior_mean=-1.0,-1.0
    for low,high,n,total in artifact['blocks']:
        require(finite(low) and finite(high) and 0<=low<=high<=1 and low>prior_high
                and type(n) is int and n>0 and type(total) is int and 0<=total<=n
                and total/n>=prior_mean, "Invalid isotonic calibration blocks")
        prior_high,prior_mean=high,total/n
    for low,high,n,total in artifact["blocks"]:
        if score <= high:
            return total/n
    return artifact["blocks"][-1][3]/artifact["blocks"][-1][2]


def report(cases: list[dict], predictions: list[dict], run: dict, manifest: dict,
           *, calibrator: dict | None=None, threshold: float | None=None, include_errors=True) -> dict:
    require(bool(cases) and len(cases)==len(predictions), "Report requires paired cases")
    require(threshold is None or (finite(threshold) and 0 <= threshold <= 1 and calibrator is not None), "Thresholding requires scoped calibrated confidence")
    n,accepted,wrong = len(cases),0,0
    urgent_total = urgent_found = false_alerts = nonurgent_total = 0
    urgent_priority_found = urgent_auto_found = review_tp = review_expected = review_predicted = 0
    confusion = Counter()
    errors=[]
    error_counts=Counter()
    accepted_flags=[]
    raw_scores, calibrated, outcomes=[],[],[]
    for c,p in zip(cases,predictions):
        require(c["case_id"]==p["case_id"], "Case order mismatch")
        label = c["label"]
        is_correct = correct(label,p)
        score = p["confidence_score"]
        cs = None
        if p["status"]=="ok" and score is not None and run["score_kind"] in ("model_probability","uncalibrated_score"):
            raw_scores.append(score)
            outcomes.append(int(is_correct))
            if calibrator is not None:
                cs = calibrated_score(calibrator,score,run,manifest)
                calibrated.append(cs)
        accept = p["status"]=="ok" and p["action"]=="AUTO_ROUTE" and (threshold is None or (cs is not None and cs>=threshold))
        accepted += int(accept)
        accepted_flags.append(accept)
        wrong += int(accept and not is_correct)
        urgent = label["priority"]=="URGENT"
        known_nonurgent = label['priority'] in ORDINAL and not urgent
        urgent_total += int(urgent)
        nonurgent_total += int(known_nonurgent)
        urgent_found += int(urgent and p["safety_signal"])
        urgent_priority_found += int(urgent and p['status']=='ok' and p['priority']=='URGENT')
        urgent_auto_found += int(urgent and accept and p['priority']=='URGENT')
        review_expected += int(label['action']=='NEEDS_REVIEW')
        review_predicted += int(not accept)
        review_tp += int(label['action']=='NEEDS_REVIEW' and not accept)
        false_alerts += int(known_nonurgent and p["safety_signal"])
        confusion[(str(label["priority"]),str(p["priority"]))] += 1
        kinds=[]
        if p["status"]!="ok": kinds.append(p["status"])
        if urgent and not p["safety_signal"]: kinds.append("missed_urgent")
        if known_nonurgent and p["safety_signal"]: kinds.append("false_safety_alert")
        if accept and not is_correct: kinds.append("unsafe_auto_route")
        if label["action"]=="AUTO_ROUTE" and not accept: kinds.append("deferred_answerable")
        for axis in ("issue_type","department","priority"):
            if label[axis]!=p[axis]: kinds.append(axis+"_mismatch")
        if label["priority"] in ORDINAL and p["priority"] in ORDINAL:
            delta=ORDINAL.index(p["priority"])-ORDINAL.index(label["priority"])
            if delta: kinds.append("over_triage" if delta>0 else "under_triage")
        if kinds and include_errors:
            errors.append({"case_id":c["case_id"],"kinds":kinds}) # No report text or source identity.
        error_counts.update(kinds)
    axes = {axis:label_metrics([str(c['label'][axis]) for c in cases],
                              [str(p[axis]) if p['status']=='ok' else '__FAILED__' for p in predictions])
            for axis in ("issue_type","department","priority")}
    axes['action']=label_metrics([c['label']['action'] for c in cases],
                                ['AUTO_ROUTE' if a else 'NEEDS_REVIEW' for a in accepted_flags])
    return {"status":"RESEARCH_ONLY","is_model_evaluation":manifest["origins"]==["real_world"],
            "candidate_fingerprint":candidate_fingerprint(run),"dataset_id":manifest["dataset_id"],"n":n,
            "axes":axes,"coverage":accepted/n,"accepted":accepted,"selective_errors":wrong,
            "selective_error":wrong/accepted if accepted else None,"selective_error_ci95":wilson(wrong,accepted),
            "urgent_support":urgent_total,"urgent_found":urgent_found,"urgent_recall":urgent_found/urgent_total if urgent_total else None,
            "urgent_recall_ci95":wilson(urgent_found,urgent_total),"false_safety_alerts":false_alerts,
            "urgent_priority_recall":urgent_priority_found/urgent_total if urgent_total else None,
            "urgent_auto_route_recall":urgent_auto_found/urgent_total if urgent_total else None,
            "review_recall":review_tp/review_expected if review_expected else None,
            "review_precision":review_tp/review_predicted if review_predicted else None,
            "known_nonurgent_support":nonurgent_total,"unassessed_priority_support":n-urgent_total-nonurgent_total,
            "false_alert_rate":false_alerts/nonurgent_total if nonurgent_total else None,
            "false_alert_rate_ci95":wilson(false_alerts,nonurgent_total),"error_counts":dict(error_counts),
            "failure_rate":sum(p["status"]!="ok" for p in predictions)/n,
            "priority_confusion":[{"expected":a,"predicted":b,"n":count} for (a,b),count in sorted(confusion.items())],
            "raw_calibration":calibration_metrics(raw_scores,outcomes) if raw_scores else None,
            "calibrated_validation":calibration_metrics(calibrated,outcomes) if calibrated else None,
            "mean_latency_seconds":sum(p["latency_seconds"] for p in predictions)/n,"errors":errors}


def score_work(bundle: Path, split: str, predictions: list[dict], run: dict, **kwargs) -> dict:
    require(split in ("development","validation"), "Calibration is fit-only; independent test requires the custodian workflow")
    manifest,cases = load_work(bundle,split)
    ordered = validate_predictions(cases,predictions,run,manifest['files'][f'work/{split}.jsonl'])
    result=report(cases,ordered,run,manifest,**kwargs)
    result.update(split=split,partition_sha256=run['partition_sha256'],predictions_sha256=digest(predictions))
    return result


def threshold_curve(bundle: Path, predictions: list[dict], run: dict, calibrator: dict, thresholds: list[float]) -> list[dict]:
    return [{"threshold":t,"metrics":score_work(bundle,"validation",predictions,run,calibrator=calibrator,threshold=t,include_errors=False)}
            for t in thresholds]


def select_threshold(curve: list[dict], *, max_error: float, min_urgent_recall: float, min_accepted: int, min_urgent: int,
                     max_false_alert_rate: float, min_coverage: float) -> dict | None:
    require(all(finite(v) and 0<=v<=1 for v in (max_error,min_urgent_recall,max_false_alert_rate,min_coverage))
            and type(min_accepted) is int and min_accepted>0 and type(min_urgent) is int and min_urgent>0,
            "Selection criteria and support requirements must be explicit")
    eligible=[]
    for row in curve:
        m=row['metrics']
        require(m.get('split')=='validation', "Threshold selection is validation-only")
        if release_criteria_met(m,{'max_error':max_error,'min_urgent_recall':min_urgent_recall,
                                  'min_accepted':min_accepted,'min_urgent':min_urgent,
                                  'max_false_alert_rate':max_false_alert_rate,'min_coverage':min_coverage}):
            eligible.append(row)
    return max(eligible,key=lambda r:(r['metrics']['coverage'],r['threshold'])) if eligible else None


def release_criteria_met(m: dict, criteria: dict) -> bool:
    return bool(m['accepted']>=criteria['min_accepted'] and m['urgent_support']>=criteria['min_urgent']
                and m['coverage']>=criteria['min_coverage']
                and m['false_alert_rate_ci95'] and m['false_alert_rate_ci95'][1]<=criteria['max_false_alert_rate']
                and m['selective_error_ci95'] and m['selective_error_ci95'][1]<=criteria['max_error']
                and m['urgent_recall_ci95'] and m['urgent_recall_ci95'][0]>=criteria['min_urgent_recall'])


def compare_candidates(bundle: Path, runs: list[dict], split='validation') -> dict:
    """Paired comparisons, preserving model sizes and common adapter semantics."""
    require(split in ('development','validation') and len(runs)>=2, "Compare at least two candidates on work data")
    manifest,cases=load_work(bundle,split)
    require(len({r['run']['candidate_id'] for r in runs})==len(runs), "Candidate IDs must be distinct")
    require(len({r['run']['adapter_version'] for r in runs})==1, "Routing adapters differ; comparisons would be confounded")
    results=[]
    ordered=[]
    for candidate in runs:
        predictions=validate_predictions(cases,candidate['predictions'],candidate['run'],manifest['files'][f'work/{split}.jsonl'])
        ordered.append(predictions)
        results.append(score_work(bundle,split,candidate['predictions'],candidate['run'],include_errors=False))
    paired=[]
    for i in range(len(runs)):
        for j in range(i+1,len(runs)):
            left_only=right_only=0
            for case,a,b in zip(cases,ordered[i],ordered[j]):
                ac,bc=correct(case['label'],a),correct(case['label'],b)
                left_only+=int(ac and not bc)
                right_only+=int(bc and not ac)
            n=left_only+right_only
            p=min(1.0,2*sum(math.comb(n,k) for k in range(min(left_only,right_only)+1))/(2**n)) if n else 1.0
            paired.append({'left':runs[i]['run']['candidate_id'],'right':runs[j]['run']['candidate_id'],
                           'left_only_correct':left_only,'right_only_correct':right_only,'mcnemar_exact_p':p})
    return {'status':'RESEARCH_ONLY','split':split,'dataset_id':manifest['dataset_id'],'results':results,'paired':paired,
            'latency_comparable':len({digest(r['run']['environment']) for r in runs})==1,'production_promotion':False}

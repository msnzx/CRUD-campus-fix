"""Ragas-compatible sample mapping and explicitly selected deterministic metrics.

Native provider-based Ragas execution remains gated. Exact match and ID relevance
metrics use supplied references only; no semantic or faithfulness scores guessed.
"""
from __future__ import annotations

from typing import Any


def _require_ragas() -> Any:
    """Import and return the ragas module, or raise a clear error."""
    try:
        import ragas  # type: ignore[import-untyped]
    except ImportError:
        raise ImportError(
            "The 'ragas' package is required for evaluation but is not installed. "
            "Keep evaluation packages separate from the locked runtime."
        )
    return ragas


def run_ragas_evaluation(
    questions: list[str],
    answers: list[str],
    contexts: list[list[str]],
    ground_truths: list[str],
    *, offline_metrics: tuple[str, ...] | None = None,
) -> dict:
    """Run explicitly selected offline metrics, otherwise refuse default providers.

    All four lists must have the same length.  Each element of ``contexts``
    is a list of retrieved-context strings for that question.

    Raises ``RuntimeError`` before imports/provider construction; execution is gated.
    Raises ``ValueError`` if inputs are inconsistent.
    """
    if not all(isinstance(v,list) for v in (questions,answers,contexts,ground_truths)):
        raise ValueError('Evaluation inputs must be lists')
    if not questions or len(questions)>200: raise ValueError('Evaluation size must be 1..200')
    if not (len(questions) == len(answers) == len(contexts) == len(ground_truths)):
        raise ValueError(
            f"Input length mismatch: questions={len(questions)}, "
            f"answers={len(answers)}, contexts={len(contexts)}, "
            f"ground_truths={len(ground_truths)}"
        )

    samples = ragas_samples(questions,answers,contexts,ground_truths)
    if offline_metrics is not None:
        if offline_metrics != ('exact_match',):
            raise ValueError('Only explicit offline exact_match is supported here')
        scores=[float(row['response']==row['reference']) for row in samples]
        return {'engine':'campusfix-offline-v1','schema':'ragas.SingleTurnSample',
                'sample_count':len(samples),'metrics':{'exact_match':sum(scores)/len(scores)},
                'per_sample':[{'index':i,'exact_match':score} for i,score in enumerate(scores)],
                'not_evaluated':['faithfulness','answer_relevancy','semantic_similarity'],
                'model_calls':0}
    # Ragas evaluate() may construct default external LLM/embedding providers.
    # Installing an optional package must never grant permission to call them.
    raise RuntimeError(
        "Ragas execution disabled: explicit approved offline providers are not configured"
    )


def ragas_samples(questions,answers,contexts,ground_truths):
    """SingleTurnSample field mapping; raw evidence stays in caller memory.

    This does not import Ragas, generate labels, write data or select providers.
    Optional native Ragas can validate these using SingleTurnSample(**row).
    """
    values=(questions,answers,contexts,ground_truths)
    if not all(isinstance(v,list) for v in values) or not 1<=len(questions)<=200 or any(len(v)!=len(questions) for v in values):
        raise ValueError('Invalid evaluation batch')
    rows=[]
    for question,answer,context,reference in zip(*values):
        if not all(isinstance(v,str) and len(v)<=32000 for v in (question,answer,reference)) or not question.strip():
            raise ValueError('Invalid evaluation text')
        if not isinstance(context,list) or len(context)>20 or any(not isinstance(v,str) or len(v)>16000 for v in context):
            raise ValueError('Invalid evaluation contexts')
        rows.append(dict(user_input=question,response=answer,retrieved_contexts=list(context),reference=reference))
    return rows


def evaluate_retrieval_ids(retrieved_ids,reference_ids):
    """ID precision/recall against explicit adjudicated relevance, never overlap guesses.

    No references means undefined recall; no retrieved IDs means precision is
    undefined. Callers must supply source IDs, not report text or new labels.
    """
    if not all(isinstance(v,list) and len(v)<=200 and all(isinstance(x,str) and 1<=len(x)<=128 for x in v)
               and len(set(v))==len(v) for v in (retrieved_ids,reference_ids)):
        raise ValueError('Invalid relevance IDs')
    relevant=set(reference_ids);hits=sum(value in relevant for value in retrieved_ids)
    return {'id_precision':hits/len(retrieved_ids) if retrieved_ids else None,
            'id_recall':hits/len(reference_ids) if reference_ids else None,
            'reference_count':len(reference_ids),'retrieved_count':len(retrieved_ids),
            'judgment_source':'CALLER_SUPPLIED_RELEVANCE_IDS','model_calls':0}

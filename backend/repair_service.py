from __future__ import annotations

import uuid

from backend.db import (
    ProofAttempt,
    RepairAttempt,
    create_repair_attempt,
    repair_to_dict,
)
from backend.repair import build_repair_candidate, build_repair_prompt, build_retry_prompt
from worker.verify import verify_lean


def create_repairs_for_attempt(attempt: ProofAttempt, max_rounds: int) -> dict[str, object]:
    base_prompt = build_repair_prompt(attempt)
    prompt = base_prompt
    repairs: list[RepairAttempt] = []

    for round_index in range(1, max_rounds + 1):
        candidate = build_repair_candidate(attempt, prompt)
        result = verify_lean(candidate.code)
        status = "candidate_verified" if result.status == "verified" else "candidate_failed"
        result_payload = result.to_dict()
        if candidate.error:
            result_payload["provider_error"] = candidate.error
        if candidate.raw_text:
            result_payload["provider_raw_text"] = candidate.raw_text

        repair = create_repair_attempt(
            repair_id=str(uuid.uuid4()),
            proof_attempt_id=attempt.id,
            prompt=prompt,
            candidate_code=candidate.code,
            provider=candidate.provider,
            model=candidate.model,
            round_index=round_index,
            status=status,
            result=result_payload,
        )
        repairs.append(repair)

        if result.status == "verified" or candidate.provider == "local":
            break

        prompt = build_retry_prompt(
            base_prompt=base_prompt,
            failed_candidate=candidate.code,
            verification_result=result_payload,
            round_index=round_index,
        )

    final_repair = repairs[-1]
    payload = repair_to_dict(final_repair)
    payload["rounds"] = [repair_to_dict(repair) for repair in repairs]
    return payload

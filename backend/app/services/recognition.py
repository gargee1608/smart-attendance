"""Match decision for SFace cosine similarity.

OpenCV's published cosine operating point for SFace is 0.363. Scores below the
org threshold, or too close to a second person, stay unknown. A name is returned
only for a known result.
"""

from dataclasses import dataclass


@dataclass
class Decision:
    result: str
    confidence: float | None
    person_id: str | None = None
    person_name: str | None = None
    person_code: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "confidence": self.confidence,
            "person_id": self.person_id,
            "person_name": self.person_name,
            "person_code": self.person_code,
            "reason": self.reason,
        }


def decide_match(ranked: list[tuple[str, str, str, float]], threshold: float, margin: float) -> Decision:
    if not ranked:
        return Decision(result="unknown", confidence=0.0, reason="no_gallery_match")
    top_id, top_name, top_code, top_score = ranked[0]
    if top_score < threshold:
        return Decision(result="unknown", confidence=float(top_score), reason="below_threshold")
    if len(ranked) > 1:
        second_score = ranked[1][3]
        if second_score >= threshold and (top_score - second_score) < margin:
            return Decision(result="unknown", confidence=float(top_score), reason="ambiguous")
    return Decision(
        result="known",
        confidence=float(top_score),
        person_id=top_id,
        person_name=top_name,
        person_code=top_code,
    )

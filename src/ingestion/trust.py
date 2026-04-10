from __future__ import annotations


class TrustScorer:
    def __init__(self) -> None:
        self.suspicious_markers = [
            "ignore previous instructions",
            "system prompt",
            "jailbreak",
            "admin command",
        ]

    def score_chunk(self, text: str) -> dict[str, object]:
        lowered = text.lower()
        hits = sum(1 for marker in self.suspicious_markers if marker in lowered)
        trust_score = max(0.0, 1.0 - (0.35 * hits))
        quarantined = trust_score < 0.5
        return {
            "trust_score": round(trust_score, 4),
            "quarantined": quarantined,
            "marker_hits": hits,
        }


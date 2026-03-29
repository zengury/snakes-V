"""ChainScorer — validates tuning quality at chain level."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from manastone.common.models import CommissioningResult

if TYPE_CHECKING:
    from manastone.profiles.profile import TuningProfile


class ChainScorer:
    """Scores chain-level tuning quality.

    Mock scoring from SPEC DD-C08:
    - Base score: 80.0
    - Adjacent Kp ratio check: >1.5x deducts 5 per pair
    - Weighted joint average (root 0.3 → tip 0.05)
    - final = mock_checks * 0.4 + joint_avg * 0.6
    """

    def __init__(self, profile: "TuningProfile") -> None:
        self.profile = profile

    def validate(
        self,
        chain_name: str,
        joint_results: Dict[str, CommissioningResult],
        action: str = "stand_single_leg",
        mock: bool = True,
    ) -> float:
        if mock:
            return self._mock_score(joint_results)
        else:
            raise NotImplementedError("Real chain validation is implemented in Phase 3")

    def _mock_score(self, joint_results: Dict[str, CommissioningResult]) -> float:
        """Compute mock chain score from joint results."""
        score = 80.0
        joint_names = list(joint_results.keys())

        # Adjacent Kp ratio check
        for i in range(len(joint_names) - 1):
            r1 = joint_results[joint_names[i]]
            r2 = joint_results[joint_names[i + 1]]
            kp1, kp2 = r1.base_pid.kp, r2.base_pid.kp
            if kp1 > 0 and kp2 > 0:
                ratio = max(kp1 / kp2, kp2 / kp1)
                if ratio > 1.5:
                    score -= 5.0

        mock_checks = max(0.0, score)

        # Weighted joint average (root=0.3, tip=0.05 linearly spaced)
        n = len(joint_names)
        if n == 0:
            return 0.0

        weights = [0.3 - 0.25 * i / max(n - 1, 1) for i in range(n)]
        total_w = sum(weights)
        # M2 fix: guard against degenerate configs where total_w could be zero.
        # Mathematically with n>=1 and the formula above total_w >= 0.05,
        # but defensive programming prevents a ZeroDivisionError on bad config.
        if total_w <= 0.0:
            return 0.0
        joint_avg = (
            sum(w * joint_results[jn].best_score for jn, w in zip(joint_names, weights))
            / total_w
        )

        return min(100.0, mock_checks * 0.4 + joint_avg * 0.6)

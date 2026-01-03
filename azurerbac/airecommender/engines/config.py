"""Engine configuration constants.

Centralized configuration for all recommendation engines.
This consolidates thresholds, defaults, and tuning parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# =============================================================================
# Global Defaults
# =============================================================================

# Default number of results to return from recommend() methods
DEFAULT_TOP_K: Final[int] = 5

# Score normalization output range (60-95%)
SCORE_FLOOR: Final[float] = 0.60
SCORE_CEILING: Final[float] = 0.95


# =============================================================================
# Per-Engine Confidence Thresholds
# =============================================================================
# These are minimum raw scores below which results are filtered out.
# Values are tuned based on each engine's scoring characteristics.


@dataclass(frozen=True, slots=True)
class EngineThresholds:
    """Confidence thresholds for a recommendation engine."""

    min_confidence: float
    """Minimum raw score to include in results."""


# Semantic embedding similarity (cosine similarity 0-1)
SEMANTIC_THRESHOLDS: Final = EngineThresholds(min_confidence=0.3)

# Cross-encoder reranking (logit scores, can be negative)
CROSSENCODER_THRESHOLDS: Final = EngineThresholds(min_confidence=0.1)

# ColBERT MaxSim scores (typically 0-40+ range)
COLBERT_THRESHOLDS: Final = EngineThresholds(min_confidence=0.1)

# HyDE hypothetical document embeddings
HYDE_THRESHOLDS: Final = EngineThresholds(min_confidence=0.2)

# LLM-based recommendations
LLM_THRESHOLDS: Final = EngineThresholds(min_confidence=0.4)


# =============================================================================
# TF-IDF Specific Configuration
# =============================================================================


@dataclass(frozen=True, slots=True)
class TFIDFConfig:
    """Configuration for TF-IDF based engines."""

    min_confidence: float
    """Minimum absolute score to include."""

    high_confidence: float
    """Score above which results are considered high confidence."""

    relative_cutoff: float
    """Relative score cutoff (fraction of best score)."""


TFIDF_CONFIG: Final = TFIDFConfig(
    min_confidence=0.5,
    high_confidence=0.9,
    relative_cutoff=0.7,
)


# =============================================================================
# Sigmoid Normalization Parameters
# =============================================================================
# Used by engines with unbounded raw scores (ColBERT, cross-encoder)


@dataclass(frozen=True, slots=True)
class SigmoidParams:
    """Parameters for sigmoid score normalization."""

    midpoint: float
    """Score at which sigmoid outputs 0.5."""

    steepness: float
    """Controls transition sharpness (higher = sharper)."""


# ColBERT MaxSim scores typically range 15-35
COLBERT_SIGMOID: Final = SigmoidParams(midpoint=25.0, steepness=0.15)


# =============================================================================
# Score Combination Weights
# =============================================================================
# Weights for combining scores from different retrieval stages.
# Higher weight = more influence on final score.


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """Weights for combining multiple retrieval stage scores."""

    primary_weight: float
    """Weight for the primary/more accurate score."""

    secondary_weight: float
    """Weight for the secondary/faster score."""

    def __post_init__(self) -> None:
        """Validate weights sum to 1.0."""
        total = self.primary_weight + self.secondary_weight
        if abs(total - 1.0) > 0.001:
            msg = f"Weights must sum to 1.0, got {total}"
            raise ValueError(msg)


# Hybrid engine: TF-IDF (curated patterns) weighted higher than embeddings
HYBRID_WEIGHTS: Final = ScoreWeights(primary_weight=0.7, secondary_weight=0.3)

# Cross-encoder: CE reranking weighted higher than bi-encoder similarity
CROSSENCODER_WEIGHTS: Final = ScoreWeights(primary_weight=0.7, secondary_weight=0.3)

# Cross-encoder logit scores typically range -5 to +5
CROSSENCODER_SIGMOID: Final = SigmoidParams(midpoint=0.0, steepness=1.0)

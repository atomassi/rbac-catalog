"""Engine configuration constants.

Centralized configuration for all recommendation engines. This module defines:
- Score normalization parameters
- Engine-specific thresholds
- Weight combinations for hybrid approaches
- TF-IDF scoring weights with validation

All engine configuration should be defined here to avoid scattered magic numbers.
"""

import math
from dataclasses import dataclass
from typing import Final

# Score normalization output range (60-95%)
SCORE_FLOOR: Final[float] = 0.60
SCORE_CEILING: Final[float] = 0.95


@dataclass(frozen=True, slots=True)
class EngineThresholds:
    """Confidence thresholds for a recommendation engine."""

    min_confidence: float


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


@dataclass(frozen=True, slots=True)
class TFIDFWeights:
    """Weights for TF-IDF multi-signal scoring.

    All weights must sum to 1.0 for proper score combination.
    Validation is performed at construction time.
    """

    bm25: float = 0.30
    """BM25 text search weight."""

    pattern: float = 0.45
    """USE_CASE_PATTERNS matching weight (curated, most reliable)."""

    name_match: float = 0.15
    """Direct role name matching weight."""

    fuzzy: float = 0.10
    """Fuzzy/abbreviation matching weight."""

    def __post_init__(self) -> None:
        """Validate weights sum to 1.0."""
        total = self.bm25 + self.pattern + self.name_match + self.fuzzy
        if not math.isclose(total, 1.0, rel_tol=1e-5):
            raise ValueError(f"TFIDFWeights must sum to 1.0, got {total:.6f}")


# Default TF-IDF weights for EnhancedTFIDFRecommender
DEFAULT_TFIDF_WEIGHTS: Final = TFIDFWeights()


@dataclass(frozen=True, slots=True)
class SigmoidParams:
    """Parameters for sigmoid score normalization.

    The sigmoid function: output = 1 / (1 + exp(-steepness * (score - midpoint)))
    """

    midpoint: float
    """Score at which sigmoid outputs 0.5."""

    steepness: float
    """Controls transition sharpness (higher = sharper)."""

    output_min: float = 0.30
    """Minimum output score."""

    output_max: float = 0.97
    """Maximum output score."""


# ColBERT MaxSim scores typically range 15-35 based on empirical testing:
# - Raw scores ~28+ are excellent matches (->90-97%)
# - Raw scores ~22-28 are good matches (->80-90%)
# - Raw scores ~15-22 are moderate matches (->55-80%)
# - Raw scores <15 are poor matches (->30-55%)
COLBERT_SIGMOID: Final = SigmoidParams(
    midpoint=18.0,
    steepness=0.30,
    output_min=0.30,
    output_max=0.97,
)


@dataclass(frozen=True, slots=True)
class WeightPair:
    """Primary/secondary weight combination (must sum to 1.0)."""

    primary: float
    secondary: float

    def __post_init__(self) -> None:
        """Validate weights sum to 1.0."""
        total = self.primary + self.secondary
        if not math.isclose(total, 1.0, rel_tol=1e-5):
            raise ValueError(f"WeightPair must sum to 1.0, got {total:.6f}")


# Score combination weights
HYBRID_WEIGHTS: Final = WeightPair(primary=0.7, secondary=0.3)  # TF-IDF / Embeddings
CROSSENCODER_WEIGHTS: Final = WeightPair(primary=0.7, secondary=0.3)  # Reranking / Bi-encoder

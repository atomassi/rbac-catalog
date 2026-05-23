"""Azure RBAC prompts for Ollama LLM."""

import re
from typing import Final

# ChatML / Qwen control tokens that must never appear in user-supplied text,
# otherwise the user can close the user turn and inject a new system prompt.
_CHATML_TOKEN_RE: Final = re.compile(
    r"<\|(?:im_start|im_end|im_sep|endoftext|system|user|assistant)\|>",
    re.IGNORECASE,
)


def _sanitize_query(query: str) -> str:
    """Remove ChatML/Qwen control tokens from a user-supplied query.

    The fine-tuned model is wrapped with a ChatML prompt template. Allowing
    raw ``<|im_start|>`` / ``<|im_end|>`` tokens in the user message lets a
    caller break out of the user turn and inject arbitrary system instructions
    or assistant output, which is then parsed back as a "recommendation".
    """
    return _CHATML_TOKEN_RE.sub("", query)


def build_role_recommendation_prompt(query: str) -> str:
    """Build prompt for fine-tuned qwen-rbac model.

    The model was trained using Qwen2.5 ChatML format with:
    - System prompt defining the task
    - User message containing just the query
    - Assistant response as structured JSON

    Args:
        query: User's natural language query. ChatML control tokens are
            stripped before interpolation to prevent prompt injection.

    Returns:
        Formatted prompt using ChatML template
    """
    system_prompt = """You are an Azure RBAC role recommender. \
Given a user's access requirement, output a JSON object with:
- role: The exact Azure built-in role name
- confidence_class: very_low, low, medium, high, or very_high
- signals_matched: Keywords from the query that match the role
- signals_missing: Important context that was missing (for low confidence)

Never recommend the Owner role. Follow least-privilege principle."""

    safe_query = _sanitize_query(query)

    return f"""<|im_start|>system
{system_prompt}<|im_end|>
<|im_start|>user
{safe_query}<|im_end|>
<|im_start|>assistant
"""

"""Azure RBAC prompts for Ollama LLM."""


def build_role_recommendation_prompt(query: str) -> str:
    """Build prompt for fine-tuned qwen-rbac model.

    The model was trained using Qwen2.5 ChatML format with:
    - System prompt defining the task
    - User message containing just the query
    - Assistant response as structured JSON

    Args:
        query: User's natural language query

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

    return f"""<|im_start|>system
{system_prompt}<|im_end|>
<|im_start|>user
{query}<|im_end|>
<|im_start|>assistant
"""

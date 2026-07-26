"""LLM-as-judge grading helper."""

from __future__ import annotations

import json
import os

from anthropic import Anthropic

from cheapquant_fi.evals.models import CriterionResult


def judge_response(
    question: str,
    answer: str,
    rubric: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
) -> CriterionResult:
    """Grade an answer using Claude as a judge.

    Args:
        question: the original user question
        answer: the agent's answer
        rubric: evaluation criteria (e.g., "Is the answer clear and accurate?")
        model: which Claude model to use for judging (cheap/fast by default)

    Returns:
        CriterionResult with pass/detail from the judge's reasoning
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return CriterionResult(
            name=f"llm_judge({rubric[:30]}...)",
            passed=False,
            detail="ANTHROPIC_API_KEY not set; cannot run LLM judge",
        )

    client = Anthropic(api_key=api_key)

    prompt = f"""\
Question: {question}

Agent's Answer:
{answer}

Evaluation Criteria:
{rubric}

Based on the criteria above, evaluate whether the agent's answer passes or fails.
Reply with a JSON object: {{"pass": true/false, "reasoning": "your explanation"}}
"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        # Extract JSON from response
        try:
            result = json.loads(text)
            passed = bool(result.get("pass", False))
            reasoning = result.get("reasoning", "")
        except json.JSONDecodeError:
            # Try to find JSON in the text
            import re

            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                passed = bool(result.get("pass", False))
                reasoning = result.get("reasoning", "")
            else:
                passed = False
                reasoning = f"Could not parse judge response: {text[:100]}"

        detail = f"Judge reasoning: {reasoning}"
        return CriterionResult(
            name=f"llm_judge({rubric[:30]}...)",
            passed=passed,
            detail=detail,
        )
    except Exception as e:
        return CriterionResult(
            name=f"llm_judge({rubric[:30]}...)",
            passed=False,
            detail=f"Error during judgment: {e}",
        )

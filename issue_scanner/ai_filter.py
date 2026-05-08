import json
import os

import anthropic
import structlog

from models import RawIssue, ScoredIssue

log = structlog.get_logger()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = """You are evaluating GitHub issues for suitability for an AI coding agent called Devin.

Score each issue from 1-10 based on:
- Clarity: Is the problem clearly described? (2 pts)
- Reproduction: Are there clear steps to reproduce or verify? (2 pts)
- Scope: Is it isolated and well-bounded, not requiring deep architectural knowledge? (3 pts)
- Complexity: Is it low complexity — a bug fix, small feature, typo, missing test, doc update? (3 pts)

Ideal issues: typos, missing imports, simple bug fixes, adding tests, documentation fixes, small UI tweaks.
Poor issues: architectural changes, large refactors, security issues, issues requiring deep domain expertise.

Return ONLY a valid JSON array, no markdown, no other text:
[{"number": 123, "score": 8.5, "reasoning": "one sentence explanation"}, ...]"""


async def score_issues(issues: list[RawIssue], top_n: int = 5) -> list[ScoredIssue]:
    if not issues:
        return []

    client = anthropic.AsyncAnthropic()

    issues_data = [
        {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body[:600],
            "labels": issue.labels,
        }
        for issue in issues
    ]

    log.info("ai_filter_start", issue_count=len(issues), model=ANTHROPIC_MODEL)

    message = await client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Score these {len(issues_data)} GitHub issues:\n\n"
                    + json.dumps(issues_data, indent=2)
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    scores_data: list[dict] = json.loads(raw)
    score_map = {item["number"]: item for item in scores_data}

    scored: list[ScoredIssue] = []
    for issue in issues:
        if issue.number in score_map:
            item = score_map[issue.number]
            scored.append(
                ScoredIssue(
                    **issue.model_dump(),
                    ai_score=float(item["score"]),
                    ai_reasoning=item["reasoning"],
                )
            )

    scored.sort(key=lambda x: x.ai_score, reverse=True)
    result = scored[:top_n]
    log.info("ai_filter_complete", top_n=len(result), scores=[i.ai_score for i in result])
    return result

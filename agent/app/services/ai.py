"""AI generation service.

Thin wrapper around an OpenAI-compatible chat completion API. Used by the
prepare workflow to generate cover letters and predict interview questions.
All prompts are self-contained — no conversation history is maintained.
"""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.settings import Settings
from app.state.prepare import SeekJobDetail

log = logging.getLogger("ai")


def _build_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )


async def generate_cover_letter(
    settings: Settings,
    *,
    job: SeekJobDetail,
    profile: dict,
) -> str:
    client = _build_client(settings)
    prefs = profile.get("proposal_preferences", {})
    tone = prefs.get("tone", "consultative, senior, practical")
    max_words = prefs.get("max_words", 320)

    # Format experience as readable bullets
    experience_lines = []
    for exp in profile.get("experience", [])[:5]:
        title = exp.get("title", "")
        company = exp.get("company", "")
        period = exp.get("period", "")
        highlights = exp.get("highlights", [])[:4]
        line = f"- {title} at {company}{f' ({period})' if period else ''}"
        for h in highlights:
            line += f"\n    • {h}"
        experience_lines.append(line)

    projects = profile.get("selected_projects", [])
    project_lines = [
        f"- {p.get('name')}: {p.get('summary', '')}" for p in projects
    ]

    skills = ", ".join(profile.get("core_strengths", []))
    experience_text = "\n".join(experience_lines) or "Not provided"
    projects_text = "\n".join(project_lines) or "None listed"

    profile_block = f"""Name: {profile.get('name')}
Headline: {profile.get('headline')}
Summary: {profile.get('summary', '')}

Experience:
{experience_text}

Selected projects:
{projects_text}

Skills: {skills}"""

    # ── Pass 1: extract the strongest matching angles ──────────────────────
    match_system = (
        "You are a job application strategist. "
        "Your job is to read a candidate profile and a job description, "
        "then identify the 3 strongest, most specific connections between them. "
        "Each connection must cite a real item from the profile (role, project, or skill) "
        "and map it to a specific requirement or theme in the job description. "
        "Return ONLY a numbered list of 3 talking points. No preamble, no commentary."
    )
    match_user = f"""JOB: {job.title} at {job.company}
{job.description[:3000]}

CANDIDATE PROFILE:
{profile_block}

List the 3 strongest specific connections between this candidate and this job."""

    log.info("[generate_cover_letter] pass1 job=%s company=%s", job.title, job.company)
    match_response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": match_system},
            {"role": "user", "content": match_user},
        ],
        temperature=0.2,
        max_tokens=400,
        _call_name=f"cover_letter pass1 — {job.title}",
    )
    talking_points = match_response.choices[0].message.content or ""
    log.debug("[generate_cover_letter] talking_points:\n%s", talking_points)

    # ── Pass 2: write the cover letter from the matched points ─────────────
    write_system = (
        f"You write concise, senior-level cover letters. Tone: {tone}. "
        f"Maximum {max_words} words. "
        "Use only the talking points provided — do not add claims not in the list. "
        "Open directly with the strongest point. No 'I am excited to apply', "
        "no generic openers. No sign-off line."
    )
    write_user = f"""Write a cover letter body for {profile.get('name')} applying to {job.title} at {job.company}.

Use these specific talking points as the backbone — address them in order:
{talking_points}

Write only the letter body."""

    log.info("[generate_cover_letter] pass2 job=%s | tone=%s max_words=%d", job.title, tone, max_words)
    write_response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": write_system},
            {"role": "user", "content": write_user},
        ],
        temperature=0.3,
        max_tokens=600,
        _call_name=f"cover_letter pass2 — {job.title}",
    )

    letter = write_response.choices[0].message.content or ""
    log.info("[generate_cover_letter] done job=%s | words=%d", job.title, len(letter.split()))
    log.debug("[generate_cover_letter] letter:\n%s", letter)
    return letter


async def predict_questions(
    settings: Settings,
    *,
    job: SeekJobDetail,
    profile: dict,
) -> list[dict[str, str]]:
    client = _build_client(settings)

    system = (
        "You are an interview preparation coach. "
        "Given a job description and candidate profile, predict the 5 most likely "
        "interview questions and provide concise, honest, tailored answers. "
        "Return a JSON array: [{\"question\": \"...\", \"answer\": \"...\"}]. "
        "Return ONLY valid JSON, no markdown, no explanation."
    )

    user = f"""Job: {job.title} at {job.company}
Description (excerpt): {job.description[:2000]}

Candidate: {profile.get('name')} — {profile.get('headline')}
Experience: {', '.join(e['title'] + ' at ' + e['company'] for e in profile.get('experience', [])[:3])}

Predict 5 interview questions and draft answers."""

    log.info("[predict_questions] job=%s company=%s", job.title, job.company)
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.5,
        max_tokens=1200,
        _call_name=f"predict_questions — {job.title}",
    )

    raw = response.choices[0].message.content or "[]"
    log.debug("[predict_questions] raw:\n%s", raw)
    try:
        questions = json.loads(raw)
        if not isinstance(questions, list):
            log.warning("[predict_questions] expected list, got %s", type(questions).__name__)
            return []
        result = [q for q in questions if isinstance(q, dict) and "question" in q and "answer" in q]
        log.info("[predict_questions] done job=%s | count=%d", job.title, len(result))
        return result
    except json.JSONDecodeError:
        log.warning("[predict_questions] JSON parse failed for job=%s", job.title)
        return []

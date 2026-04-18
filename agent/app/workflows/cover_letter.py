"""LangGraph workflow for cover letter generation.

Nodes:
  extract_requirements  → LLM: pull key themes/skills from the job description
  match_profile         → LLM: find the best available profile evidence per requirement
  evaluate_fit          → LLM: score overall fit; route to write or gaps summary
  write_draft           → LLM: write the letter from matched evidence
  check_length          → deterministic: trim to max_words if over limit

Conditional edge after evaluate_fit:
  fit_score >= 0.5  →  write_draft → check_length → END
  fit_score < 0.5   →  END (cover_letter = "", gaps populated)

Callers should check CoverLetterResult.is_suitable before using the letter.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

log = logging.getLogger("cover_letter")

from langgraph.graph import StateGraph, END

_TITLE_WORDS = {
    "recruiter", "recruitment", "manager", "team", "hr", "hiring",
    "talent", "acquisition", "coordinator", "specialist", "officer",
    "department", "admin", "administrator", "contact", "enquiries",
    # executive titles — not someone to address a cover letter to
    "ceo", "cto", "coo", "cfo", "founder", "co-founder", "director",
    "president", "executive", "vp", "vice", "principal", "owner",
    "head", "chief", "partner",
}

def _is_real_name(value: str) -> bool:
    """Return True only if value looks like a person's name, not a job title or team."""
    if not value or not value.strip():
        return False
    lower = value.strip().lower()
    # Reject if any word is a known title/team word
    words = re.split(r"[\s,]+", lower)
    if any(w in _TITLE_WORDS for w in words):
        return False
    # Must contain at least one letter-only word (a name, not an email/number)
    if not any(re.match(r"^[a-z]+$", w) for w in words):
        return False
    return True
from pydantic import BaseModel, Field
from openai import AsyncOpenAI

from app.settings import Settings
from app.state.prepare import SeekJobDetail


# ── State ──────────────────────────────────────────────────────────────────

class CoverLetterState(BaseModel):
    # Inputs
    job_title: str
    job_company: str
    job_description: str
    job_salary: str | None = None

    name: str
    headline: str
    summary: str
    narrative_strengths_text: str  # pre-written evidence sentences grounded in real work
    experience_text: str
    projects_text: str
    skills: str

    tone: str = "consultative, senior, practical"
    max_words: int = 320
    writing_samples: list[str] = Field(default_factory=list)  # user's own sentences to mirror
    voice_profile: dict[str, Any] = Field(default_factory=dict)

    # Pre-populated from cache (skips parse_jd LLM call when set)
    cached_must_have: list[str] = Field(default_factory=list)
    cached_duties: list[str] = Field(default_factory=list)
    cached_nice_to_have: list[str] = Field(default_factory=list)
    cached_contact_name: str = ""

    # Intermediate outputs
    contact_name: str = ""         # hiring manager name if found in JD
    requirements: str = ""        # must-have only
    bonus_requirements: str = ""  # nice-to-have (not used for fit scoring)
    evidence: str = ""
    fit_score: float = 1.0          # 0.0–1.0 from evaluate_fit
    fit_verdict: str = ""           # "suitable" | "not_suitable"
    gaps: list[str] = Field(default_factory=list)
    draft: str = ""

    # Final output
    cover_letter: str = ""
    word_count: int = 0
    is_suitable: bool = True


# ── Result returned to callers ─────────────────────────────────────────────

class CoverLetterResult(BaseModel):
    is_suitable: bool
    cover_letter: str = ""          # empty when not suitable
    gaps: list[str] = Field(default_factory=list)   # populated when not suitable
    evidence: str = ""              # match evidence lines: [STRONG/MODERATE/WEAK] req → proof


# ── Graph ──────────────────────────────────────────────────────────────────

def build_cover_letter_graph(settings: Settings) -> Any:
    client = AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )

    # ── Node 1: parse_jd ───────────────────────────────────────────────────
    async def parse_jd(state: CoverLetterState) -> dict:
        """Split the JD into must-have requirements vs duties vs nice-to-have.
        Uses pre-parsed cache when available, otherwise calls the LLM."""
        if state.cached_must_have:
            log.info("[parse_jd] cache hit job=%s must_have=%d duties=%d nice_to_have=%d",
                     state.job_title, len(state.cached_must_have),
                     len(state.cached_duties), len(state.cached_nice_to_have))
            must_have = state.cached_must_have
            duties = state.cached_duties
            nice_to_have = state.cached_nice_to_have
            contact_name = state.cached_contact_name if _is_real_name(state.cached_contact_name) else ""
            requirements = "\n".join(f"{i+1}. {r}" for i, r in enumerate(must_have))
            bonus = "\n".join(f"- {r}" for r in nice_to_have)
            return {"requirements": requirements, "bonus_requirements": bonus, "contact_name": contact_name}

        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Parse a job description into structured data.\n"
                        "Return JSON with exactly these keys:\n"
                        '{"must_have": ["..."], "duties": ["..."], "nice_to_have": ["..."], "contact_name": "...", "contact_confidence": "high|low"}\n'
                        "must_have: skills, experience, qualifications the candidate must bring. "
                        "Include years of experience, specific tools, technical skills, domain knowledge. "
                        "duties: what the person will actually do in the role day-to-day. "
                        "nice_to_have: bonus, preferred, or optional items explicitly marked as such. "
                        'contact_name: The name of the person to address the cover letter to, '
                        'or "" if none is found. '
                        'ONLY use a name if it appears near application-related language — '
                        'phrases like "contact [name]", "reach out to [name]", "applications to [name]", '
                        '"speak to [name]", "ask [name]", "managed by [name] who is hiring". '
                        'If the person is mentioned in a company bio, leadership section, award, '
                        '"About Us", or founding story — even if they are the CEO or founder — use "". '
                        'The test is context, not title: a founder who says "send your CV directly to me" counts; '
                        'a recruiter mentioned only in an award paragraph does not. '
                        'contact_confidence: "high" if you are certain this person is the application contact, '
                        '"low" if you are guessing or the context is ambiguous. '
                        "Return ONLY the JSON object."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Job: {state.job_title} at {state.job_company}\n\n{state.job_description[:3000]}",
                },
            ],
            temperature=0.1,
            max_tokens=600,
        )
        import json as _json
        raw = (response.choices[0].message.content or "{}").strip()
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
        try:
            parsed = _json.loads(raw)
            must_have = parsed.get("must_have", [])
            nice_to_have = parsed.get("nice_to_have", [])
            duties = parsed.get("duties", [])
            raw_contact = parsed.get("contact_name", "")
            confidence = parsed.get("contact_confidence", "low")
            contact_name = raw_contact if (_is_real_name(raw_contact) and confidence == "high") else ""
            requirements = "\n".join(f"{i+1}. {r}" for i, r in enumerate(must_have))
            bonus = "\n".join(f"- {r}" for r in nice_to_have)
        except Exception:
            log.warning("[parse_jd] JSON parse failed, using raw LLM output as requirements")
            requirements = raw
            bonus = ""
            duties = []
            contact_name = ""

        log.info("[parse_jd] job=%s | must_have=%d duties=%d nice_to_have=%d contact=%r",
                 state.job_title, len(must_have) if 'must_have' in dir() else '?',
                 len(duties), len(nice_to_have) if 'nice_to_have' in dir() else '?',
                 contact_name)
        log.debug("[parse_jd] must_have:\n%s", requirements)
        log.debug("[parse_jd] nice_to_have:\n%s", bonus)
        return {"requirements": requirements, "bonus_requirements": bonus, "contact_name": contact_name}

    # ── Node 2: extract_requirements (now reads structured requirements) ────
    # Skipped — parse_jd already produces structured requirements.
    # Kept as a no-op alias for graph compatibility.
    async def extract_requirements(state: CoverLetterState) -> dict:
        return {}  # requirements already set by parse_jd

    # ── Node 2: match_profile ──────────────────────────────────────────────
    async def match_profile(state: CoverLetterState) -> dict:
        """Find the best available match for each requirement.
        Always return a match — the fit evaluator will judge quality, not this node."""
        profile_block = f"""Name: {state.name}
Headline: {state.headline}

Narrative evidence (pre-written, grounded in real work — prefer these over raw experience lines):
{state.narrative_strengths_text}

Experience (★ = quantified metric):
{state.experience_text}

Projects:
{state.projects_text}

Skills: {state.skills}"""

        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You match a candidate profile to job requirements. "
                        "For each requirement, find the CLOSEST matching item from the profile "
                        "(a role, project, skill, or summary point). "
                        "Always pick something — your job is to find the best available evidence, "
                        "not to judge fit. Rate each match as STRONG, MODERATE, or WEAK. "
                        "Prefer evidence lines marked ★ (quantified metrics) — cite the number directly. "
                        "Format each item as: [STRONG/MODERATE/WEAK] Requirement → Evidence\n"
                        "Do not say 'no match' or 'no evidence'. Always cite the closest item."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"REQUIREMENTS:\n{state.requirements}\n\n"
                        f"CANDIDATE PROFILE:\n{profile_block}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=600,
        )
        evidence = response.choices[0].message.content or ""
        strong = sum(1 for l in evidence.splitlines() if "[STRONG]" in l)
        moderate = sum(1 for l in evidence.splitlines() if "[MODERATE]" in l)
        weak = sum(1 for l in evidence.splitlines() if "[WEAK]" in l)
        log.info("[match_profile] job=%s | STRONG=%d MODERATE=%d WEAK=%d",
                 state.job_title, strong, moderate, weak)
        log.debug("[match_profile] evidence:\n%s", evidence)
        return {"evidence": evidence}

    # ── Node 3: evaluate_fit ───────────────────────────────────────────────
    async def evaluate_fit(state: CoverLetterState) -> dict:
        """Score overall fit and identify gaps. Routes to write or gaps summary."""
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You evaluate how well a candidate matches a job based on the evidence mapping provided. "
                        "Return JSON with exactly these keys:\n"
                        '{"fit_score": 0.0-1.0, "verdict": "suitable" or "not_suitable", '
                        '"gaps": ["gap 1", "gap 2"]}\n'
                        "fit_score >= 0.5 means suitable (enough evidence to write a credible letter). "
                        "Score STRONG matches highly. MODERATE matches still count — they show transferable skills. "
                        "Only list gaps for must-have requirements that are clearly missing. "
                        "Do not list gaps for bonus/nice-to-have items. "
                        "gaps should name the specific missing must-have skills/experience, max 3 items. "
                        "Return ONLY the JSON object, no other text."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Job: {state.job_title} at {state.job_company}\n\n"
                        f"Evidence mapping:\n{state.evidence}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=200,
        )

        raw = response.choices[0].message.content or "{}"
        # Strip markdown code fences if present
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()

        try:
            import json
            parsed = json.loads(raw)
            fit_score = float(parsed.get("fit_score", 0.5))
            verdict = parsed.get("verdict", "suitable")
            gaps = parsed.get("gaps", [])
        except Exception:
            fit_score = 0.5
            verdict = "suitable"
            gaps = []

        is_suitable = fit_score >= 0.5
        log.info("[evaluate_fit] job=%s | score=%.2f verdict=%s suitable=%s gaps=%s",
                 state.job_title, fit_score, verdict, is_suitable, gaps)
        log.debug("[evaluate_fit] raw LLM output: %s", raw)
        return {
            "fit_score": fit_score,
            "fit_verdict": verdict,
            "gaps": gaps,
            "is_suitable": is_suitable,
        }

    # ── Node 4: evaluate_and_write ─────────────────────────────────────────
    # Runs evaluate_fit and write_draft in parallel (both need only `evidence`).
    # Saves ~10s vs sequential execution on suitable jobs.
    async def evaluate_and_write(state: CoverLetterState) -> dict:
        import asyncio

        strong_evidence = "\n".join(
            line for line in state.evidence.splitlines()
            if "[STRONG]" in line or "[MODERATE]" in line
        )
        if not strong_evidence:
            strong_evidence = state.evidence

        async def _evaluate_fit() -> dict:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You evaluate how well a candidate matches a job based on the evidence mapping provided. "
                            "Return JSON with exactly these keys:\n"
                            '{"fit_score": 0.0-1.0, "verdict": "suitable" or "not_suitable", '
                            '"gaps": ["gap 1", "gap 2"]}\n'
                            "fit_score >= 0.5 means suitable (enough evidence to write a credible letter). "
                            "Score STRONG matches highly. MODERATE matches still count — they show transferable skills. "
                            "Only list gaps for must-have requirements that are clearly missing. "
                            "Do not list gaps for bonus/nice-to-have items. "
                            "gaps should name the specific missing must-have skills/experience, max 3 items. "
                            "Return ONLY the JSON object, no other text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Job: {state.job_title} at {state.job_company}\n\n"
                            f"Evidence mapping:\n{state.evidence}"
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=200,
            )
            raw = (response.choices[0].message.content or "{}").strip()
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
            try:
                import json
                parsed = json.loads(raw)
                fit_score = float(parsed.get("fit_score", 0.5))
                verdict = parsed.get("verdict", "suitable")
                gaps = parsed.get("gaps", [])
            except Exception:
                fit_score = 0.5
                verdict = "suitable"
                gaps = []
            is_suitable = fit_score >= 0.5
            log.info("[evaluate_fit] job=%s | score=%.2f verdict=%s suitable=%s gaps=%s",
                     state.job_title, fit_score, verdict, is_suitable, gaps)
            return {"fit_score": fit_score, "fit_verdict": verdict, "gaps": gaps, "is_suitable": is_suitable}

        async def _write_draft() -> str:
            # Build voice block — writing_samples first (most direct signal),
            # fall back to summary (also written by the candidate in their own words)
            voice_lines: list[str] = []
            if state.voice_profile:
                voice_lines.append("VOICE PROFILE — stable writing habits to preserve:")
                tone_labels = state.voice_profile.get("tone_labels") or []
                if tone_labels:
                    voice_lines.append(f"  tone_labels: {', '.join(tone_labels)}")
                for key in (
                    "formality",
                    "sentence_style",
                    "opening_style",
                ):
                    value = state.voice_profile.get(key)
                    if value:
                        voice_lines.append(f"  {key}: {value}")
                for key in ("uses_contractions", "prefers_first_person"):
                    value = state.voice_profile.get(key)
                    if isinstance(value, bool):
                        voice_lines.append(f"  {key}: {'yes' if value else 'no'}")
                avoid = state.voice_profile.get("avoid") or []
                if avoid:
                    voice_lines.append(f"  avoid: {', '.join(avoid)}")
            if state.writing_samples:
                voice_lines.append("VOICE SAMPLES — sentences this person has actually written:")
                for s in state.writing_samples:
                    voice_lines.append(f'  "{s}"')
            elif state.summary:
                voice_lines.append("VOICE SAMPLE — how this person writes about themselves:")
                voice_lines.append(f'  "{state.summary[:600]}"')
            voice_block = "\n".join(voice_lines)

            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are ghostwriting a cover letter. Your job is to make it sound like the candidate "
                            "wrote it themselves, not like an AI produced it.\n\n"
                            "VOICE — this is the most important instruction:\n"
                            "Study the voice samples provided. Mirror the candidate's actual writing style: "
                            "their sentence rhythm, how they open sentences, vocabulary level, use of contractions, "
                            "how direct or reflective they are, whether they use 'I've' vs 'I have', etc. "
                            "The letter should read as if the candidate typed it, not as if a system generated it. "
                            "Adapt phrasing from the samples naturally — do not copy them verbatim.\n\n"
                            "STRUCTURE:\n"
                            f"3 short paragraphs, each 2–4 sentences. Target {state.max_words} words total. "
                            "Paragraphs should flow naturally from the candidate's voice — do not make them "
                            "formulaic or symmetrical. They can vary in length and approach.\n\n"
                            "CONTENT RULES:\n"
                            "- Talking points are your only source of facts. Do not invent claims.\n"
                            "- Write ONLY about what the job asked for. Do not volunteer unrelated skills.\n"
                            "- Every claim must be anchored to a result, not just an activity.\n"
                            "  BAD: 'I built a data pipeline using Databricks and dbt'\n"
                            "  GOOD: 'I built a data pipeline using Databricks and dbt that cut data latency from 4h to 30min'\n"
                            "  If a talking point has a ★ metric, use the number. If it doesn't, state the business impact.\n"
                            "- At least one sentence per paragraph must state what changed or improved, not just what was built.\n\n"
                            "STYLE RULES:\n"
                            "- No buzzwords: leverage, utilize, passionate, excited, innovative, transformative, "
                            "robust, spearhead, streamline, synergy, cutting-edge, foster, impactful, drive results.\n"
                            "- No self-praise labels: 'strong communicator', 'team player', 'fast learner'.\n"
                            "- No dashes as mid-sentence punctuation (— or –). Use a comma or new sentence instead.\n"
                            "- No greeting, no sign-off, no 'I am writing to apply'.\n"
                            "Return ONLY the 3 paragraphs separated by blank lines."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{voice_block}\n\n"
                            f"Now write the cover letter body for {state.name} applying to "
                            f"{state.job_title} at {state.job_company}.\n\n"
                            f"Talking points — pre-written sentences grounded in real work. "
                            f"Use these as the factual basis. Keep the substance, adapt the phrasing to match the voice above:\n"
                            f"{strong_evidence}"
                        ),
                    },
                ],
                temperature=0.6,
                max_tokens=600,
            )
            draft = response.choices[0].message.content or ""
            log.info("[write_draft] job=%s | words=%d", state.job_title, len(draft.split()))
            return draft

        fit_result, draft = await asyncio.gather(_evaluate_fit(), _write_draft())

        if not fit_result["is_suitable"]:
            # Job is not suitable — discard the draft we speculatively generated
            log.info("[evaluate_and_write] job=%s not suitable, discarding draft", state.job_title)
            return {**fit_result, "draft": ""}

        return {**fit_result, "draft": draft}

    # ── Node 5: check_length ───────────────────────────────────────────────
    def check_length(state: CoverLetterState) -> dict:
        body = state.draft.strip()
        word_count = len(body.split())

        if word_count > state.max_words:
            # Trim paragraph by paragraph to preserve structure
            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
            kept: list[str] = []
            total = 0
            for para in paragraphs:
                para_words = para.split()
                if total + len(para_words) <= state.max_words:
                    kept.append(para)
                    total += len(para_words)
                else:
                    # Fit as many words of this paragraph as possible, ending on a sentence
                    remaining = state.max_words - total
                    if remaining > 0:
                        truncated = " ".join(para_words[:remaining])
                        m = re.search(r"[.!?][^.!?]*$", truncated)
                        if m:
                            truncated = truncated[: m.start() + 1].strip()
                        if truncated:
                            kept.append(truncated)
                    break
            body = "\n\n".join(kept)
            log.info("[check_length] job=%s | trimmed %d→%d words", state.job_title, word_count, len(body.split()))

        # Strip any em/en dashes that slipped through
        body = body.replace("—", ",").replace("–", ",")

        # Build greeting — only use contact_name if it's a real person's name
        if _is_real_name(state.contact_name):
            first_name = state.contact_name.split()[0]
            greeting = f"Hi {first_name},"
        else:
            greeting = "Hi Recruitment Manager,"

        sign_off = f"Regards,\n{state.name}"
        cover_letter = f"{greeting}\n\n{body}\n\n{sign_off}"

        return {"cover_letter": cover_letter, "word_count": len(body.split())}

    # ── Assemble ───────────────────────────────────────────────────────────
    graph = StateGraph(CoverLetterState)
    graph.add_node("parse_jd", parse_jd)
    graph.add_node("match_profile", match_profile)
    graph.add_node("evaluate_and_write", evaluate_and_write)
    graph.add_node("check_length", check_length)

    graph.set_entry_point("parse_jd")
    graph.add_edge("parse_jd", "match_profile")
    graph.add_edge("match_profile", "evaluate_and_write")

    def route_after_evaluate_and_write(state: CoverLetterState) -> Literal["check_length", "__end__"]:
        return "check_length" if state.is_suitable else END

    graph.add_conditional_edges("evaluate_and_write", route_after_evaluate_and_write)
    graph.add_edge("check_length", END)

    return graph.compile()


# ── Helpers ────────────────────────────────────────────────────────────────

def _format_experience(profile: dict) -> str:
    lines = []
    for exp in profile.get("experience", [])[:8]:
        line = f"- {exp.get('title', '')} at {exp.get('company', '')}"
        period = exp.get("period", "")
        if period:
            line += f" ({period})"
        for h in exp.get("highlights", [])[:3]:
            line += f"\n    • {h}"
        for m in exp.get("metrics", [])[:3]:
            line += f"\n    ★ {m}"
        lines.append(line)
    return "\n".join(lines) or "Not provided"


def _format_projects(profile: dict) -> str:
    lines = [
        f"- {p.get('name')}: {p.get('summary', '')}"
        for p in profile.get("selected_projects", [])
    ]
    return "\n".join(lines) or "None listed"


def _format_narrative_strengths(profile: dict) -> str:
    items = profile.get("narrative_strengths", [])
    if not items:
        return ""
    return "\n".join(f"- {s}" for s in items)


# ── Public entry point ─────────────────────────────────────────────────────

async def run_cover_letter(
    settings: Settings,
    *,
    job: SeekJobDetail,
    profile: dict,
    cached_analysis=None,
) -> CoverLetterResult:
    prefs = profile.get("proposal_preferences", {})
    writing_samples = (
        profile.get("writing_samples")
        or profile.get("voice_samples")
        or [
            item.get("tone_sample", "")
            for item in profile.get("evidence_items", [])
            if isinstance(item, dict) and item.get("tone_sample")
        ]
    )

    initial = CoverLetterState(
        job_title=job.title,
        job_company=job.company,
        job_description=cached_analysis.description if cached_analysis and cached_analysis.description else job.description,
        job_salary=job.salary,
        name=profile.get("name", ""),
        headline=profile.get("headline", ""),
        summary=profile.get("summary", ""),
        narrative_strengths_text=_format_narrative_strengths(profile),
        experience_text=_format_experience(profile),
        projects_text=_format_projects(profile),
        skills=", ".join(profile.get("core_strengths", [])),
        tone=prefs.get("tone", "consultative, senior, practical"),
        max_words=prefs.get("max_words", 320),
        writing_samples=writing_samples,
        voice_profile=profile.get("voice_profile", {}),
        cached_must_have=cached_analysis.must_have if cached_analysis else [],
        cached_duties=cached_analysis.duties if cached_analysis else [],
        cached_nice_to_have=cached_analysis.nice_to_have if cached_analysis else [],
        cached_contact_name=cached_analysis.contact_name if cached_analysis else "",
    )

    graph = build_cover_letter_graph(settings)
    result = await graph.ainvoke(initial.model_dump())
    state = CoverLetterState.model_validate(result)

    return CoverLetterResult(
        is_suitable=state.is_suitable,
        cover_letter=state.cover_letter,
        gaps=state.gaps,
        evidence=state.evidence,
    )

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
    personal_statement: str
    narrative_strengths_text: str  # pre-written evidence sentences grounded in real work
    experience_text: str
    projects_text: str
    skills: str

    tone: str = "consultative, senior, practical"
    max_words: int = 320

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
                        '{"must_have": ["..."], "duties": ["..."], "nice_to_have": ["..."], "contact_name": "..."}\n'
                        "must_have: skills, experience, qualifications the candidate must bring. "
                        "Include years of experience, specific tools, technical skills, domain knowledge. "
                        "duties: what the person will actually do in the role day-to-day. "
                        "nice_to_have: bonus, preferred, or optional items explicitly marked as such. "
                        'contact_name: ONLY a real person\'s name (e.g. "Jane Smith" or "Jane") if explicitly named in the JD. '
                        'If only a job title, team name, or email address is given (e.g. "contact our Talent team", "ask the recruiter"), use "". '
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
            contact_name = raw_contact if _is_real_name(raw_contact) else ""
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
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Write a cover letter body in a {state.tone} tone. "
                            f"Target {state.max_words} words. "
                            "Use exactly 3 paragraphs separated by blank lines (\\n\\n).\n"
                            "Paragraph 1: one direct sentence naming the specific fit — what the candidate has that this role needs.\n"
                            "Paragraph 2: 2–3 sentences of concrete evidence from the talking points — name actual tools, "
                            "projects, or outcomes. Be specific, not vague.\n"
                            "Paragraph 3: 1–2 sentences on broader relevant experience, then one short closing sentence.\n\n"
                            "CONTENT RULES — these override everything else:\n"
                            "- Write ONLY about topics that appear in the job requirements. "
                            "Do not volunteer skills or experience areas the job did not ask for, "
                            "even if the candidate has them. Match the employer's scope, not the candidate's full breadth.\n"
                            "- The talking points are your only source of facts. Do not invent claims or add context "
                            "from the candidate's voice section — that section sets writing style only, not content.\n\n"
                            "STYLE RULES:\n"
                            "- Write like a senior engineer writing a direct email, not like an AI writing a cover letter.\n"
                            "- Vary sentence length: mix short punchy sentences with longer ones. Avoid a uniform rhythm.\n"
                            "- Use first person naturally. Contractions are fine (I've, I'm, it's).\n"
                            "- Be specific. Name tools, systems, outcomes — never say 'various technologies' or 'multiple projects'.\n"
                            "- No hyphens or dashes of any kind: do NOT use — or – or - as punctuation mid-sentence. "
                            "Rewrite those phrases as separate sentences or use 'and', 'which', 'where', or a comma instead.\n"
                            "- No buzzwords: do NOT use leverage, utilize, passionate, excited, dynamic, innovative, "
                            "transformative, robust, spearhead, streamline, synergy, cutting-edge, foster, facilitate, "
                            "thrive, impactful, drive results, or any similar corporate filler.\n"
                            "- No self-praise: avoid 'strong communicator', 'team player', 'fast learner', 'detail-oriented'.\n"
                            "- No greeting, no sign-off, no 'I am writing to apply'. Return ONLY the 3 paragraphs."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Write the cover letter body for {state.name} applying to "
                            f"{state.job_title} at {state.job_company}.\n\n"
                            f"Candidate's voice (use this to set tone — do not quote directly):\n{state.personal_statement}\n\n"
                            f"Matched talking points — these are pre-written sentences grounded in real work. "
                            f"Use them as the basis for your paragraphs. Adapt the phrasing but keep the substance. "
                            f"Do not invent new claims or generalise into buzzwords:\n{strong_evidence}"
                        ),
                    },
                ],
                temperature=0.35,
                max_tokens=500,
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
    for exp in profile.get("experience", [])[:5]:
        line = f"- {exp.get('title', '')} at {exp.get('company', '')}"
        period = exp.get("period", "")
        if period:
            line += f" ({period})"
        for h in exp.get("highlights", [])[:4]:
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

    initial = CoverLetterState(
        job_title=job.title,
        job_company=job.company,
        job_description=cached_analysis.description if cached_analysis and cached_analysis.description else job.description,
        job_salary=job.salary,
        name=profile.get("name", ""),
        headline=profile.get("headline", ""),
        summary=profile.get("summary", ""),
        personal_statement=profile.get("personal_statement", ""),
        narrative_strengths_text=_format_narrative_strengths(profile),
        experience_text=_format_experience(profile),
        projects_text=_format_projects(profile),
        skills=", ".join(profile.get("core_strengths", [])),
        tone=prefs.get("tone", "consultative, senior, practical"),
        max_words=prefs.get("max_words", 320),
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

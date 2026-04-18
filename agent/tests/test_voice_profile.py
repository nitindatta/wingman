from app.services.voice_profile import build_voice_profile


async def test_build_voice_profile_derives_stable_style_signals() -> None:
    profile = await build_voice_profile(
        [
            "I usually start by getting close to the operational pain point, then I keep the design practical enough to ship.",
            "I've found the work goes better when the solution is clear enough for the team to operate after handover.",
            "When a system gets too abstract too early, I try to bring it back to the delivery constraint that actually matters.",
        ]
    )

    assert "practical" in profile.tone_labels
    assert "grounded" in profile.tone_labels
    assert profile.formality == "semi-formal"
    assert profile.sentence_style in {"medium", "short_to_medium"}
    assert profile.uses_contractions is True
    assert profile.prefers_first_person is True
    assert profile.opening_style in {"first_person_direct", "context_first"}
    assert profile.confidence == "medium"

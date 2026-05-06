from app.state.apply import StepInfo
from app.workflows.apply import _action_label_for_step


def test_linkedin_step_prefers_visible_next_over_default_continue() -> None:
    step = StepInfo(
        page_url="https://www.linkedin.com/jobs/view/4404477725/",
        page_type="form",
        fields=[],
        visible_actions=["Save", "I'm interested", "Next"],
    )

    assert _action_label_for_step(step, "Continue") == "Next"


def test_linkedin_step_without_navigation_action_does_not_fallback_to_continue() -> None:
    step = StepInfo(
        page_url="https://www.linkedin.com/jobs/view/4404477725/",
        page_type="form",
        fields=[],
        visible_actions=["Save", "I'm interested"],
    )

    assert _action_label_for_step(step, "Continue") is None


def test_seek_step_keeps_continue_fallback_for_legacy_pages() -> None:
    step = StepInfo(
        page_url="https://www.seek.com.au/job/123/apply",
        page_type="form",
        fields=[],
        visible_actions=[],
    )

    assert _action_label_for_step(step, "Continue") == "Continue"

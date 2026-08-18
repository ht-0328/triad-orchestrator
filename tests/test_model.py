import unittest

from triad.model import (
    DELIBERATION_STATES,
    GATE_TARGETS,
    HUMAN_GATES,
    PHASES,
    REVISION_GATES,
    State,
    resolved_agent,
)


class ModelTests(unittest.TestCase):
    def test_code_reviewer_is_not_implementation_author(self):
        self.assertEqual(resolved_agent(State.CODE_REVIEW, "claude"), "codex")
        self.assertEqual(resolved_agent(State.CODE_REVIEW, "codex"), "claude")

    def test_fixer_is_original_implementation_author(self):
        self.assertEqual(resolved_agent(State.FIX, "claude"), "claude")
        self.assertEqual(resolved_agent(State.FIX, "codex"), "codex")

    def test_codex_outputs_have_claude_review_phases(self):
        self.assertEqual(PHASES[State.TASK_BREAKDOWN].agent, "codex")
        self.assertEqual(PHASES[State.TASK_PLAN_REVIEW].agent, "claude")
        self.assertEqual(PHASES[State.DELIVERY_PREP].agent, "codex")
        self.assertEqual(PHASES[State.DELIVERY_REVIEW].agent, "claude")

    def test_plan_capstone_is_authored_by_codex_and_reviewed_by_claude(self):
        self.assertEqual(PHASES[State.PLAN].agent, "codex")
        self.assertEqual(PHASES[State.PLAN].artifact, "artifacts/plan.md")
        self.assertEqual(PHASES[State.PLAN_REVIEW].agent, "claude")
        self.assertTrue(PHASES[State.PLAN_REVIEW].review)

    def test_three_ai_deliberation_precedes_only_human_gate_before_build(self):
        self.assertEqual(PHASES[State.INTAKE].agent, "codex")
        self.assertEqual(PHASES[State.INTAKE].artifact, "input/intake.md")
        self.assertEqual(PHASES[State.RESEARCH].agent, "antigravity")
        self.assertEqual(PHASES[State.SOLUTION_PROPOSAL].agent, "claude")
        self.assertEqual(PHASES[State.PROPOSAL_REVIEW].agent, "codex")
        self.assertEqual(PHASES[State.PROPOSAL_FACT_CHECK].agent, "antigravity")
        self.assertEqual(PHASES[State.SYNTHESIS].agent, "codex")
        self.assertEqual(PHASES[State.SYNTHESIS_REVIEW].agent, "claude")
        self.assertEqual(PHASES[State.REQUIREMENTS].agent, "claude")
        self.assertEqual(PHASES[State.DESIGN].agent, "claude")
        self.assertEqual(
            HUMAN_GATES["plan"],
            (State.AWAITING_PLAN_APPROVAL, State.TASK_BREAKDOWN),
        )

    def test_human_gates_are_exactly_plan_and_delivery(self):
        self.assertEqual(set(HUMAN_GATES), {"plan", "delivery"})
        self.assertEqual(
            HUMAN_GATES["delivery"],
            (State.AWAITING_DELIVERY_APPROVAL, State.DELIVERED),
        )
        self.assertEqual(
            set(REVISION_GATES.values()),
            {"plan", "delivery"},
        )

    def test_deliberation_states_defer_human_decisions_through_plan_review(self):
        self.assertIn(State.REQUIREMENTS, DELIBERATION_STATES)
        self.assertIn(State.DESIGN, DELIBERATION_STATES)
        self.assertIn(State.PLAN, DELIBERATION_STATES)
        self.assertIn(State.PLAN_REVIEW, DELIBERATION_STATES)
        self.assertNotIn(State.TASK_BREAKDOWN, DELIBERATION_STATES)
        self.assertNotIn(State.IMPLEMENTATION, DELIBERATION_STATES)

    def test_plan_gate_freezes_all_fourteen_deliberation_artifacts(self):
        self.assertEqual(len(GATE_TARGETS["plan"]), 14)
        self.assertEqual(GATE_TARGETS["plan"][0], "input/intake.md")
        self.assertEqual(GATE_TARGETS["plan"][-1], "reviews/plan-review.md")

    def test_delivery_gate_freezes_build_and_review_evidence(self):
        self.assertIn("evidence/build-test.md", GATE_TARGETS["delivery"])
        self.assertIn("artifacts/delivery-summary.md", GATE_TARGETS["delivery"])


if __name__ == "__main__":
    unittest.main()

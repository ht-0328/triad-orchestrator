import json
import unittest
from pathlib import Path

from triad.adapters import Adapter
from triad.policy import PolicyError


PAYLOAD = {
    "summary": "done",
    "content": "evidence",
    "verdict": "not_applicable",
    "human_decisions": [],
}


class AdapterTests(unittest.TestCase):
    def test_extracts_direct_payload(self):
        self.assertEqual(Adapter._extract_payload(json.dumps(PAYLOAD)), PAYLOAD)

    def test_extracts_claude_style_result(self):
        wrapped = {"result": json.dumps(PAYLOAD), "is_error": False}
        self.assertEqual(Adapter._extract_payload(json.dumps(wrapped)), PAYLOAD)

    def test_extracts_last_jsonl_event(self):
        raw = json.dumps({"event": "start"}) + "\n" + json.dumps({"structured_output": PAYLOAD})
        self.assertEqual(Adapter._extract_payload(raw), PAYLOAD)

    def test_rejects_wrong_types_and_extra_properties(self):
        wrong_type = {**PAYLOAD, "human_decisions": "none"}
        extra = {**PAYLOAD, "unexpected": True}
        self.assertIsNone(Adapter._extract_payload(json.dumps(wrong_type)))
        self.assertIsNone(Adapter._extract_payload(json.dumps(extra)))

    def test_unknown_agent_error_is_japanese(self):
        adapter = Adapter(Path(__file__).resolve().parent.parent)
        with self.assertRaisesRegex(PolicyError, "不明なAIです"):
            adapter.run_artifact("unknown", "prompt", Path.cwd(), 1)


if __name__ == "__main__":
    unittest.main()

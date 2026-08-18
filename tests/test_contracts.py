import json
import unittest
from pathlib import Path

from triad.model import State


ROOT = Path(__file__).resolve().parent.parent


class ContractTests(unittest.TestCase):
    def test_state_schema_matches_python_enum(self):
        schema = json.loads((ROOT / "contracts" / "state.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["state"]["enum"]), {state.value for state in State})

    def test_agent_verdict_contract_matches_adapter_validator(self):
        schema = json.loads((ROOT / "contracts" / "agent-output.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(schema["properties"]["verdict"]["enum"]),
            {"not_applicable", "approve", "needs_changes"},
        )


if __name__ == "__main__":
    unittest.main()

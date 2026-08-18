import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from triad.policy import PolicyError, scrub_environment, validate_changed_paths, validate_compose_model


class PolicyTests(unittest.TestCase):
    def test_api_keys_are_removed_from_agent_environment(self):
        env = scrub_environment(
            {
                "PATH": "/bin",
                "OPENAI_API_KEY": "secret",
                "ANTHROPIC_API_KEY": "secret",
                "GEMINI_API_KEY": "secret",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "DOCKER_HOST": "tcp://production.example:2376",
            }
        )
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["TRIAD_AGENT_RUN"], "1")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("GEMINI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertNotIn("DOCKER_HOST", env)

    def test_protected_changes_are_rejected(self):
        for path in (
            ".ai-dev/approvals/release.json",
            ".ai-dev/tasks/T-1/state.json",
            "AGENTS.md",
        ):
            with self.subTest(path=path), self.assertRaises(PolicyError):
                validate_changed_paths([path])

    def test_application_source_change_is_allowed(self):
        validate_changed_paths(["src/app.py", "compose.yaml"])

    def test_safe_compose_model_is_accepted(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            validate_compose_model(
                {
                    "services": {
                        "test": {
                            "build": {"context": str(root)},
                            "volumes": [
                                {"type": "bind", "source": str(root / "src"), "target": "/app"},
                                {"type": "volume", "source": "cache", "target": "/cache"},
                            ],
                        }
                    }
                },
                root,
            )

    def test_dangerous_compose_model_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                {"privileged": True},
                {"network_mode": "host"},
                {"volumes": [{"type": "bind", "source": "/", "target": "/host"}]},
                {"volumes": [{"type": "bind", "source": "/var/run/docker.sock", "target": "/sock"}]},
                {"devices": ["/dev/kvm"]},
                {"cap_add": ["SYS_ADMIN"]},
                {"env_file": ["/home/user/secrets.env"]},
            )
            for service in cases:
                with self.subTest(service=service), self.assertRaisesRegex(PolicyError, "安全でないDocker Composeモデル"):
                    validate_compose_model({"services": {"test": service}}, root)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.reading = yaml.load((ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.login = yaml.load((ROOT / ".github/workflows/login.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_cloud_login_has_no_public_password_or_otp_input(self):
        self.assertEqual(set(self.login["on"]["workflow_dispatch"]["inputs"]), {"resume_run_id"})
        environment = self.login["jobs"]["login"]["env"]
        self.assertEqual(environment["WXREAD_QR_PASSWORD"], "${{ secrets.WXREAD_QR_PASSWORD }}")
        self.assertEqual(self.login["permissions"], {"contents": "read", "actions": "read"})

    def test_login_and_reading_share_non_cancelling_session_lock(self):
        self.assertEqual(self.login["concurrency"], self.reading["concurrency"])
        self.assertEqual(self.login["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(self.login["jobs"]["login"]["environment"], "AutoRead")
        self.assertEqual(self.reading["jobs"]["deploy"]["environment"], "AutoRead")

    def test_public_artifacts_only_include_encrypted_session_and_viewer(self):
        steps = self.login["jobs"]["login"]["steps"]
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 2)
        for step in uploads:
            self.assertEqual(step["with"]["path"].splitlines(), [".state/qr-public/login.html", ".state/qr-public/session.enc"])
            self.assertEqual(step["with"]["retention-days"], "1")
            self.assertNotIn("login.key", step["with"]["path"])
        self.assertLess(next(i for i, item in enumerate(steps) if item.get("id") == "qr"),
                        next(i for i, item in enumerate(steps) if item.get("id") == "wait"))

    def test_user_inputs_are_passed_through_environment_not_shell_source(self):
        for definition in (self.login, self.reading):
            for job in definition["jobs"].values():
                self.assertIn("runs-on", job)
                for step in job["steps"]:
                    if "run" in step:
                        self.assertNotIn("${{ inputs.", step["run"])

    def test_cache_does_not_publish_plaintext_keys_and_login_is_save_on_success(self):
        steps = self.login["jobs"]["login"]["steps"]
        save = next(item for item in steps if item.get("uses", "").startswith("actions/cache/save@"))
        self.assertIn("authenticated == 'true'", save["if"])
        self.assertNotIn("login.key", save["with"]["path"])
        self.assertIn(".state/login.enc", save["with"]["path"])


if __name__ == "__main__":
    unittest.main()

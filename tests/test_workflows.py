import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.reading = yaml.load((ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.login = yaml.load((ROOT / ".github/workflows/login.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_cloud_login_has_no_public_password_or_otp_input(self):
        self.assertEqual(self.login["on"]["workflow_dispatch"], {})
        environment = self.login["jobs"]["login"]["env"]
        self.assertEqual(environment["WXREAD_QR_PASSWORD"], "${{ secrets.WXREAD_QR_PASSWORD }}")
        self.assertEqual(self.login["permissions"], {"contents": "read"})
        self.assertNotIn("WXREAD_LOGIN_OTP", (ROOT / ".github/workflows/login.yml").read_text(encoding="utf-8"))
        self.assertNotIn("resume_run_id", (ROOT / ".github/workflows/login.yml").read_text(encoding="utf-8"))

    def test_login_and_reading_share_non_cancelling_session_lock(self):
        self.assertEqual(self.login["concurrency"], self.reading["concurrency"])
        self.assertEqual(self.login["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(self.login["jobs"]["login"]["environment"], "AutoRead")
        self.assertEqual(self.reading["jobs"]["deploy"]["environment"], "AutoRead")

    def test_public_artifact_only_includes_encrypted_viewer(self):
        steps = self.login["jobs"]["login"]["steps"]
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        for step in uploads:
            self.assertEqual(step["with"]["path"].splitlines(), [".state/qr-public/login.html"])
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

    def test_reading_step_is_plain_python_without_a_node_cache_sdk(self):
        steps = self.reading["jobs"]["deploy"]["steps"]
        reading = next(item for item in steps if item.get("name", "").startswith("Run reading task"))
        self.assertIn("python main.py", reading["run"])
        self.assertNotIn("GITHUB_TOKEN", reading["env"])
        self.assertNotIn("WXREAD_CACHE_PREFIX", reading["env"])
        self.assertFalse(any(item.get("uses", "").startswith("actions/setup-node@") for item in steps))
        self.assertFalse(any("npm ci" in item.get("run", "") for item in steps))
        self.assertFalse(any(item.get("uses", "").startswith("./.github/actions/") for item in steps))
        self.assertFalse((ROOT / ".github/actions/reading").exists())

    def test_schedule_is_pinned_for_a_late_github_queue(self):
        self.assertEqual([item["cron"] for item in self.reading["on"]["schedule"]], ["47 8 * * *"])
        self.assertEqual(self.reading["concurrency"]["group"], "wxread-session-${{ github.repository }}")

    def test_long_wait_is_split_into_its_own_job_before_the_reading_job(self):
        window = self.reading["jobs"]["window"]
        deploy = self.reading["jobs"]["deploy"]
        self.assertEqual(window["outputs"], {"window_ok": "${{ steps.gate.outputs.window_ok }}"})
        self.assertEqual(deploy["needs"], "window")
        self.assertEqual(deploy["if"], "${{ needs.window.outputs.window_ok == 'true' }}")
        # 6 小时硬上限：两个 job 各自留足预算。
        self.assertLessEqual(int(window["timeout-minutes"]), 360)
        self.assertLessEqual(int(deploy["timeout-minutes"]), 360)

    def test_window_job_sleeps_to_the_handoff_point(self):
        steps = self.reading["jobs"]["window"]["steps"]
        gate = next(item for item in steps if item.get("id") == "gate")
        self.assertEqual(gate["run"], "python start_window.py")
        self.assertEqual(
            gate["env"],
            {
                "WXREAD_FORCE_WINDOW": "${{ inputs.force }}",
                "WXREAD_WAKE_LEAD_SECONDS": "1800",
            },
        )
        self.assertEqual(gate["timeout-minutes"], "320")

    def test_deploy_reweaves_the_clock_to_exactly_one(self):
        steps = self.reading["jobs"]["deploy"]["steps"]
        gate = next(item for item in steps if item.get("name", "").startswith("Wait until exactly 01:00"))
        self.assertEqual(gate["run"], "python start_window.py")
        self.assertEqual(gate["env"], {"WXREAD_FORCE_WINDOW": "${{ inputs.force }}"})
        reading = next(item for item in steps if item.get("name", "").startswith("Run reading task"))
        self.assertEqual(reading["if"], "${{ steps.gate.outputs.window_ok == 'true' }}")
        self.assertLess(steps.index(gate), steps.index(reading))
        self.assertTrue((ROOT / "start_window.py").is_file())

    def test_force_input_is_a_boolean_defaulting_to_off(self):
        force = self.reading["on"]["workflow_dispatch"]["inputs"]["force"]
        self.assertEqual((force["type"], force["default"]), ("boolean", "false"))

    def test_cloud_login_migrates_the_v2_cache_without_a_password_derived_key(self):
        steps = self.reading["jobs"]["deploy"]["steps"]
        migrate = next(item for item in steps if item.get("name", "").startswith("Migrate the v2"))
        self.assertEqual(migrate["with"]["restore-keys"], "${{ steps.cache.outputs.legacy_cache_prefix }}")
        self.assertEqual(Path(ROOT / "actions_login.py").read_text(encoding="utf-8").count('legacy_prefix = "wxread-login-v2-"'), 1)

    def test_checkpoint_cache_paths_match_existing_restore_version(self):
        expected = [".state/login.enc", ".state/cookies.enc", ".state/device.json"]
        for definition in (self.login, self.reading):
            for item in definition["jobs"]["login" if definition is self.login else "deploy"]["steps"]:
                if item.get("uses", "").startswith("actions/cache/") and ".state/login.enc" in item["with"]["path"]:
                    self.assertEqual(item["with"]["path"].splitlines(), expected)


if __name__ == "__main__":
    unittest.main()

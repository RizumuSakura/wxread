"""Regression guards for the removed query/statistics feature and its packaging."""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FeatureScopeTests(unittest.TestCase):
    def test_removed_cli_and_configuration_are_absent(self):
        self.assertFalse((ROOT / "official_skill.py").exists())
        self.assertFalse((ROOT / "qr_login.py").exists())

    def test_readme_does_not_advertise_removed_features(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for removed in ("指定书籍查询", "官方阅读统计", "查询指定书籍", "official_skill.py", "resume_run_id", "WXREAD_LOGIN_OTP"):
            self.assertNotIn(removed, text)
        for section in ("GitHub Actions", "READ_NUM", "故障排查", "个人书架", "二维码"):
            self.assertIn(section, text)

    def test_runtime_imports_and_docker_copy_sources_exist(self):
        for filename in ("main.py", "config.py", "weread_auth.py", "weread_shelf.py", "actions_login.py", "login_store.py", "scheduler.py"):
            tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
            for item in ast.walk(tree):
                if isinstance(item, ast.ImportFrom):
                    self.assertNotEqual(item.module, "official_skill")
                elif isinstance(item, ast.Import):
                    self.assertNotIn("official_skill", [alias.name for alias in item.names])
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
            if line.startswith("COPY "):
                for name in line.split()[1:-1]:
                    self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()

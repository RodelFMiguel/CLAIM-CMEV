"""Behaviour checks for portable skill mirroring; no agent or app is launched."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_skills.py"
SPEC = importlib.util.spec_from_file_location("sync_skills", SCRIPT)
sync_skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_skills)


class SkillSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in sync_skills.SKILL_NAMES:
            source = self.root / ".agents" / "skills" / name / "SKILL.md"
            source.parent.mkdir(parents=True)
            source.write_text(
                f"---\nname: {name}\ndescription: Test portable workflow.\n---\n\n"
                "Read the repository specification before the requested work.\n",
                encoding="utf-8",
            )

    def source(self, name=None):
        return (
            self.root / ".agents" / "skills"
            / (name or sync_skills.SKILL_NAMES[0]) / "SKILL.md"
        )

    def mirror(self, name=None):
        return (
            self.root / ".claude" / "skills"
            / (name or sync_skills.SKILL_NAMES[0]) / "SKILL.md"
        )

    def test_check_reports_missing_mirrors_without_writing(self):
        changed = sync_skills.synchronize(self.root, check=True)
        self.assertEqual(len(changed), 7)
        self.assertFalse((self.root / ".claude").exists())

    def test_write_copies_all_skills_and_is_idempotent(self):
        changed = sync_skills.synchronize(self.root, check=False)
        self.assertEqual(len(changed), 7)
        for name in sync_skills.SKILL_NAMES:
            self.assertEqual(self.source(name).read_bytes(), self.mirror(name).read_bytes())
        self.assertEqual(sync_skills.synchronize(self.root, check=True), [])
        self.assertEqual(sync_skills.synchronize(self.root, check=False), [])

    def test_detects_and_repairs_drift(self):
        sync_skills.synchronize(self.root, check=False)
        self.mirror().write_text("Changed mirror\n", encoding="utf-8")
        self.assertEqual(len(sync_skills.synchronize(self.root, check=True)), 1)
        self.assertEqual(self.mirror().read_text(encoding="utf-8"), "Changed mirror\n")
        sync_skills.synchronize(self.root, check=False)
        self.assertEqual(self.mirror().read_bytes(), self.source().read_bytes())

    def test_preserves_unrelated_configuration_and_skills(self):
        settings = self.root / ".claude" / "settings.local.json"
        unrelated = self.root / ".claude" / "skills" / "personal" / "SKILL.md"
        unrelated.parent.mkdir(parents=True)
        settings.write_text('{"personal": true}\n', encoding="utf-8")
        unrelated.write_text("Unrelated skill\n", encoding="utf-8")
        sync_skills.synchronize(self.root, check=False)
        self.assertEqual(settings.read_text(encoding="utf-8"), '{"personal": true}\n')
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "Unrelated skill\n")

    def test_supporting_resources_are_mirrored(self):
        resource = self.source().parent / "references" / "example.txt"
        resource.parent.mkdir()
        resource.write_text("A reusable example\n", encoding="utf-8")
        self.assertEqual(len(sync_skills.synchronize(self.root, check=False)), 8)
        copied = self.mirror().parent / "references" / "example.txt"
        self.assertEqual(copied.read_bytes(), resource.read_bytes())

    def test_invalid_later_source_prevents_all_writes(self):
        self.source(sync_skills.SKILL_NAMES[-1]).write_text("No frontmatter\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            sync_skills.synchronize(self.root, check=False)
        self.assertFalse((self.root / ".claude").exists())

    def test_stale_resource_is_reported_without_deletion_or_partial_updates(self):
        sync_skills.synchronize(self.root, check=False)
        stale = self.mirror(sync_skills.SKILL_NAMES[-1]).parent / "old.txt"
        stale.write_text("Retain until deliberately reconciled\n", encoding="utf-8")
        original_mirror = self.mirror().read_bytes()
        self.source().write_bytes(self.source().read_bytes() + b"Updated source\n")
        with self.assertRaisesRegex(ValueError, "Stale mirror"):
            sync_skills.synchronize(self.root, check=False)
        self.assertTrue(stale.exists())
        self.assertEqual(self.mirror().read_bytes(), original_mirror)

    def test_destination_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / ".claude"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable on this host")
        with self.assertRaisesRegex(ValueError, "link"):
            sync_skills.synchronize(self.root, check=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_cli_check_uses_script_root_and_returns_nonzero_for_drift(self):
        script = self.root / "scripts" / "sync_skills.py"
        script.parent.mkdir()
        script.write_bytes(SCRIPT.read_bytes())
        result = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=self.temporary.name,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / ".claude").exists())
        sync_skills.synchronize(self.root, check=False)
        result = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=self.root / "scripts",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tools" / "ci" / "verify_repository.py"
SPEC = importlib.util.spec_from_file_location("repository_policy", POLICY_PATH)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class RepositoryPolicyTest(unittest.TestCase):
    def test_snapshot_manifest_path_mapping(self):
        self.assertEqual(
            POLICY.snapshot_path_to_repo("manifests/source_locations.md"),
            "docs/snapshot-20260714/source_locations.md",
        )
        self.assertEqual(
            POLICY.snapshot_path_to_repo("patrol_ai/patrol_ai_runner.py"),
            "patrol_ai/patrol_ai_runner.py",
        )

    def test_map_yaml_is_detected_by_path_or_content(self):
        self.assertTrue(POLICY.is_map_yaml(Path("deploy/maps/site.yaml"), b""))
        content = b"image: site.pgm\nresolution: 0.05\norigin: [0, 0, 0]\n"
        self.assertTrue(POLICY.is_map_yaml(Path("config/site.yaml"), content))

        image_key = b"im" + b"age"
        resolution_key = b"resolu" + b"tion"
        origin_key = b"ori" + b"gin"
        quoted = (
            b'"' + image_key + b'": site.pgm\n'
            b"'" + resolution_key + b"': 0.05\n"
            b'"' + origin_key + b'": [0, 0, 0]\n'
        )
        inline = (
            b"{" + image_key + b": site.pgm, "
            + resolution_key + b": 0.05, "
            + origin_key + b": [0, 0, 0]}\n"
        )
        self.assertTrue(POLICY.is_map_yaml(Path("config/quoted.yaml"), quoted))
        self.assertTrue(POLICY.is_map_yaml(Path("config/inline.yml"), inline))
        self.assertFalse(POLICY.is_map_yaml(Path("config/app.yaml"), b"enabled: true\n"))

    def test_secret_patterns_cover_fine_grained_and_unquoted_values(self):
        fine_grained = b"github_" + b"pat_" + (b"A" * 24)
        unquoted = b"pass" + b"word: nonempty-value"
        self.assertIsNotNone(POLICY.GITHUB_FINE_GRAINED_TOKEN_RE.search(fine_grained))
        self.assertTrue(POLICY.has_unquoted_secret_assignment(unquoted))

        samples = (
            b"tok" + b"en: nonempty-value",
            b"tok" + b"en=nonempty-value",
            b"pass" + b"word = nonempty-value",
            b"cook" + b"ie=nonempty-value",
            b"api_" + b"key=nonempty-value",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(POLICY.has_unquoted_secret_assignment(sample))

        quoted = b'"' + b"tok" + b'en": "nonempty-value"'
        self.assertTrue(POLICY.has_quoted_secret_assignment(quoted))

        huawei_values = (
            b"HUAWEICLOUD_SDK_" + b"AK=nonempty-value",
            b"HUAWEICLOUD_SDK_" + b"SK: nonempty-value",
        )
        for sample in huawei_values:
            with self.subTest(sample=sample):
                self.assertTrue(POLICY.has_huawei_sdk_credential(sample))

        identity_values = (
            b"elder" + b"Code: nonempty-value",
            b"elder" + b"ProfileId: nonempty-value",
        )
        for sample in identity_values:
            with self.subTest(sample=sample):
                self.assertIsNotNone(POLICY.UNQUOTED_IDENTITY_RE.search(sample))

    def test_unquoted_secret_templates_and_environment_references_are_safe(self):
        samples = (
            b"tok" + b"en=",
            b"tok" + b"en=${TOKEN}",
            b"tok" + b"en=${{ secrets.TOKEN }}",
            b"pass" + b"word = $PASSWORD",
            b"pass" + b"word = $env:PASSWORD",
            b"cook" + b"ie={{ COOKIE }}",
            b"tok" + b"en=<set-me>",
            b"tok" + b"en=CHANGE_ME",
            b"tok" + b'en=os.getenv("TOKEN")',
            b"tok" + b'en=os.environ["TOKEN"]',
            b"tok" + b'en=System.getenv("TOKEN")',
            b"tok" + b'en=env.get("TOKEN")',
            b"tok" + b"en=process.env.TOKEN",
            b"tok" + b'en=process.env["TOKEN"]',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(POLICY.has_unquoted_secret_assignment(sample))

        quoted_templates = (
            b'"' + b"tok" + b'en": "${TOKEN}"',
            b'"' + b"tok" + b'en": "CHANGE_ME"',
            b'"' + b"cook" + b'ie": ""',
        )
        for sample in quoted_templates:
            with self.subTest(sample=sample):
                self.assertFalse(POLICY.has_quoted_secret_assignment(sample))

        unsafe_fallbacks = (
            b"pass" + b"word=${PASSWORD:-hardcoded-secret}",
            b"tok" + b'en=os.getenv("TOKEN", "hardcoded-secret")',
            b"cook" + b"ie=<hardcoded-secret>",
        )
        for sample in unsafe_fallbacks:
            with self.subTest(sample=sample):
                self.assertTrue(POLICY.has_unquoted_secret_assignment(sample))

        quoted_fallback = (
            b'"' + b"tok" + b'en": "${{ secrets.TOKEN || \'hardcoded-secret\' }}"'
        )
        self.assertTrue(POLICY.has_quoted_secret_assignment(quoted_fallback))

        quoted_continuations = (
            b"tok" + b'en="${TOKEN}" + "hardcoded-secret"',
            b"pass" + b'word="CHANGE_ME" or "hardcoded-secret"',
        )
        for sample in quoted_continuations:
            with self.subTest(sample=sample):
                self.assertTrue(POLICY.has_quoted_secret_assignment(sample))

        huawei_templates = (
            b"HUAWEICLOUD_SDK_" + b"AK=",
            b"HUAWEICLOUD_SDK_" + b"SK=${HUAWEICLOUD_SK}",
            b"HUAWEICLOUD_SDK_" + b"AK=CHANGE_ME",
        )
        for sample in huawei_templates:
            with self.subTest(sample=sample):
                self.assertFalse(POLICY.has_huawei_sdk_credential(sample))

    def test_long_base64_payload_is_detected(self):
        payloads = (
            b"A" * 4096,
            b"\n".join([b"A" * 64] * 65),
            b"\n".join([b"A" * 76] * 54),
            b"\n".join(([b"A" * 37, b"B" * 83, b"C" * 119]) * 18),
            b"\n".join([b"    " + (b"A_" * 32)] * 65),
        )
        for payload in payloads:
            with self.subTest(line_count=len(payload.splitlines())):
                self.assertTrue(POLICY.has_long_base64_payload(payload))

        self.assertFalse(POLICY.has_long_base64_payload(b"A" * 76))
        self.assertFalse(POLICY.has_long_base64_payload(b"\n".join([b"A" * 64] * 63)))
        plain_text = b"\n".join([b"this is ordinary text, not encoded data"] * 200)
        self.assertFalse(POLICY.has_long_base64_payload(plain_text))

    def test_maps_directory_only_allows_readme(self):
        errors = []
        POLICY.append_path_policy_errors(
            Path("deploy/MaPs/site.txt"), 12, "100644", "test", errors
        )
        self.assertTrue(any("maps directories" in error for error in errors))

        errors = []
        POLICY.append_path_policy_errors(
            Path("deploy/maps/README.md"), 12, "100644", "test", errors
        )
        self.assertFalse(any("maps directories" in error for error in errors))

    def test_module_scope_os_reassignment_is_rejected(self):
        sources = (
            "import os\nos = replacement\n",
            "import os\nos.getenv = replacement\n",
            'import os\nsetattr(os, "getenv", replacement)\n',
            "import os\nif enabled:\n    os = replacement\n",
            'import os\n@setattr(os, "getenv", replacement)\ndef helper():\n    pass\n',
            'import os\ndef helper(value=setattr(os, "getenv", replacement)):\n    pass\n',
            'import os\ndef helper(value: setattr(os, "getenv", replacement)):\n    pass\n',
            'import os\nhelper = lambda value=setattr(os, "getenv", replacement): value\n',
            'import os\n(lambda: setattr(os, "getenv", replacement))()\n',
            "import os\ndef mutate():\n    os.getenv = replacement\nmutate()\n",
            'import os\nclass Container:\n    setattr(os, "getenv", replacement)\n',
            "import os\nclass Container:\n    os.getenv = replacement\n",
        )
        for source in sources:
            with self.subTest(source=source):
                errors = []
                POLICY.append_module_os_integrity_errors(
                    POLICY.ast.parse(source), errors
                )
                self.assertTrue(errors)

        errors = []
        POLICY.append_module_os_integrity_errors(
            POLICY.ast.parse(
                "import os\n"
                "def helper():\n"
                "    return os.getenv('NAME')\n"
            ),
            errors,
        )
        self.assertEqual(errors, [])

    def test_commit_history_scans_messages_for_sensitive_values(self):
        commit = "a" * 40

        def fake_git_bytes(*args, **kwargs):
            if args[0] == "rev-list":
                return commit.encode("ascii") + b"\n"
            if args[:3] == ("show", "-s", "--format=%B"):
                return b"tok" + b"en: nonempty-value\n"
            if args[0] == "ls-tree":
                return b""
            self.fail("unexpected git invocation: {}".format(args))

        errors = []
        with mock.patch.object(POLICY, "git_bytes", side_effect=fake_git_bytes):
            POLICY.verify_commit_history(errors)
        self.assertTrue(any("COMMIT_MESSAGE" in error for error in errors))

    def test_commit_history_rejects_changed_workflow_blob(self):
        commit = "b" * 40
        object_id = "c" * 40
        workflow = b"name: changed\n"
        tree_entry = (
            "100644 blob {} {}\t.github/workflows/ci.yml".format(
                object_id, len(workflow)
            ).encode("ascii")
            + b"\0"
        )

        def fake_git_bytes(*args, **kwargs):
            if args[0] == "rev-list":
                return commit.encode("ascii") + b"\n"
            if args[:3] == ("show", "-s", "--format=%B"):
                return b"safe CI commit\n"
            if args[0] == "ls-tree":
                return tree_entry
            if args[0] == "cat-file":
                return workflow
            self.fail("unexpected git invocation: {}".format(args))

        errors = []
        with mock.patch.object(POLICY, "git_bytes", side_effect=fake_git_bytes):
            POLICY.verify_commit_history(errors)
        self.assertTrue(
            any("historical workflow content is not approved" in error for error in errors)
        )

    def test_workflow_content_matches_approved_hash(self):
        relative = ".github/workflows/ci.yml"
        actual = POLICY.sha256(POLICY.git_index_blob(relative))
        self.assertEqual(actual, POLICY.APPROVED_WORKFLOW_HASHES[relative])

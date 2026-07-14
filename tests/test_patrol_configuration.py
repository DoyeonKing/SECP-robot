import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "patrol_ai" / "patrol_ai_runner.py"


def _mock_identity_fields():
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "mock_recognize_face"
    )
    returns = [node for node in ast.walk(target) if isinstance(node, ast.Return)]
    assert len(returns) == 1
    assert isinstance(returns[0].value, ast.Dict)
    fields = {}
    for key, value in zip(returns[0].value.keys, returns[0].value.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            assert key.value not in fields
            fields[key.value] = value
    return fields


def _assert_empty_getenv(value, environment_name):
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Attribute)
    assert isinstance(value.func.value, ast.Name)
    assert value.func.value.id == "os"
    assert value.func.attr == "getenv"
    assert not value.keywords
    assert [argument.value for argument in value.args] == [environment_name, ""]


class PatrolConfigurationTest(unittest.TestCase):
    def test_mock_identity_fields_use_environment_variables(self):
        fields = _mock_identity_fields()
        _assert_empty_getenv(fields["elderProfileId"], "PATROL_ELDER_PROFILE_ID")
        _assert_empty_getenv(fields["elderCode"], "PATROL_ELDER_CODE")

    def test_environment_template_contains_only_empty_identity_values(self):
        lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            [
                "PATROL_ELDER_PROFILE_ID=",
                "PATROL_ELDER_CODE=",
            ],
        )

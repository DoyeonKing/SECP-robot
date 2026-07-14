import ast
import importlib.util
from pathlib import Path

from launch import LaunchContext


LAUNCH_PATH = (
    Path(__file__).resolve().parents[1]
    / 'launch'
    / 'inspection_map.launch.py'
)


def _load_launch_module():
    specification = importlib.util.spec_from_file_location(
        'inspection_map_launch_test',
        str(LAUNCH_PATH),
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _launch_context(test_data, goal_action_bridge):
    context = LaunchContext()
    context.launch_configurations['test_data'] = test_data
    context.launch_configurations['goal_action_bridge'] = goal_action_bridge
    return context


def test_offline_and_real_navigation_modes_are_mutually_exclusive():
    module = _load_launch_module()
    context = _launch_context('true', 'true')

    try:
        module._validate_navigation_mode(context)
    except RuntimeError as error:
        assert 'cannot both be true' in str(error)
    else:
        raise AssertionError('unsafe launch mode combination was accepted')

    assert module._validate_navigation_mode(
        _launch_context('true', 'false')
    ) == []
    assert module._validate_navigation_mode(
        _launch_context('false', 'true')
    ) == []


def test_test_data_launch_argument_defaults_to_false():
    syntax_tree = ast.parse(LAUNCH_PATH.read_text(encoding='utf-8'))
    for node in ast.walk(syntax_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id != 'DeclareLaunchArgument':
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != 'test_data':
            continue
        defaults = {
            keyword.arg: keyword.value
            for keyword in node.keywords
        }
        default_value = defaults['default_value']
        assert isinstance(default_value, ast.Constant)
        assert default_value.value == 'false'
        return
    raise AssertionError('test_data launch argument was not found')


def test_goal_bridge_parameters_are_forwarded_by_launch():
    source = LAUNCH_PATH.read_text(encoding='utf-8')
    goal_bridge_block = source.split('goal_bridge = Node(', 1)[1].split(
        'return LaunchDescription',
        1,
    )[0]
    for parameter_name in (
        'server_wait_timeout',
        'cooldown_sec',
        'zero_twist_repeats',
        'zero_twist_interval_sec',
        'ignore_goals_while_active',
    ):
        assert "'{}'".format(parameter_name) in goal_bridge_block

from inspection_map_bridge.rosbridge_websocket_compat import _camel_to_snake
from inspection_map_bridge.rosbridge_websocket_compat import (
    _load_class_with_action_messages,
)


def test_camel_to_snake_matches_generated_action_module():
    assert _camel_to_snake('NavigateToPose') == 'navigate_to_pose'


def test_loads_foxy_generated_feedback_message():
    message_class = _load_class_with_action_messages(
        'nav2_msgs',
        'action',
        'NavigateToPose_FeedbackMessage',
    )
    assert message_class.__name__ == 'NavigateToPose_FeedbackMessage'

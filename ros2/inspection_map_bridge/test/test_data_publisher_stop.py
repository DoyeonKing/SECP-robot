import os

os.environ['ROS_DOMAIN_ID'] = str(200 + os.getpid() % 20)
os.environ['ROS_LOCALHOST_ONLY'] = '1'

import rclpy
from action_msgs.msg import GoalStatus
from std_msgs.msg import Empty

from inspection_map_bridge.test_data_publisher import (
    InspectionMapTestDataPublisher,
)


def test_empty_stop_cancels_offline_navigation_once_and_sends_one_zero():
    rclpy.init()
    node = InspectionMapTestDataPublisher()
    velocities = []
    node._publish_velocity = (
        lambda linear, angular: velocities.append((linear, angular))
    )
    try:
        assert node._status == GoalStatus.STATUS_EXECUTING

        node._on_stop_navigation(Empty())
        node._on_stop_navigation(Empty())

        assert node._status == GoalStatus.STATUS_CANCELED
        assert node._zero_velocity_pending
        node._publish_fast_data()
        node._publish_fast_data()
        assert velocities == [(0.0, 0.0)]
    finally:
        node.destroy_node()
        rclpy.shutdown()

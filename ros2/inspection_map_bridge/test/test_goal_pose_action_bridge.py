import copy
import os
import threading
import time

os.environ['ROS_DOMAIN_ID'] = str(200 + os.getpid() % 20)
os.environ['ROS_LOCALHOST_ONLY'] = '1'

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionServer
from rclpy.action import CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import Empty

from inspection_map_bridge.goal_pose_action_bridge import BridgeState
from inspection_map_bridge.goal_pose_action_bridge import GoalPoseActionBridge


def _wait_until(predicate, timeout=5.0, message='condition was not met'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _set_parameters(bridge, **values):
    results = bridge.set_parameters(
        [Parameter(name, value=value) for name, value in values.items()]
    )
    assert all(result.successful for result in results)


def _goal(x_value=2.5, y_value=-1.25, stamp_sec=123):
    message = PoseStamped()
    message.header.frame_id = 'map'
    message.header.stamp.sec = stamp_sec
    message.header.stamp.nanosec = 456
    message.pose.position.x = x_value
    message.pose.position.y = y_value
    message.pose.orientation.w = 1.0
    return message


class _RosHarness:
    def __init__(self, execute_callback=None, cancel_callback=None):
        rclpy.init()
        self.executor = MultiThreadedExecutor(num_threads=4)
        self.publisher_node = Node('inspection_map_bridge_test_publisher')
        self.bridge = GoalPoseActionBridge()
        self.server_node = None
        self.action_server = None
        if execute_callback is not None:
            self.server_node = Node('inspection_map_fake_nav2_server')
            kwargs = {}
            if cancel_callback is not None:
                kwargs['cancel_callback'] = cancel_callback
            self.action_server = ActionServer(
                self.server_node,
                NavigateToPose,
                '/navigate_to_pose',
                execute_callback,
                callback_group=ReentrantCallbackGroup(),
                **kwargs
            )

        self.goal_publisher = self.publisher_node.create_publisher(
            PoseStamped,
            '/inspection_map/goal_pose',
            10,
        )
        self.stop_publisher = self.publisher_node.create_publisher(
            Empty,
            '/inspection_map/stop_navigation',
            10,
        )
        self.zero_twists = []
        self.publisher_node.create_subscription(
            Twist,
            '/cmd_vel',
            lambda message: self.zero_twists.append(copy.deepcopy(message)),
            10,
        )

        if self.server_node is not None:
            self.executor.add_node(self.server_node)
        self.executor.add_node(self.publisher_node)
        self.executor.add_node(self.bridge)
        self.spin_thread = threading.Thread(
            target=self.executor.spin,
            daemon=True,
        )
        self.spin_thread.start()
        _wait_until(
            lambda: self.goal_publisher.get_subscription_count() >= 1,
            message='/inspection_map/goal_pose bridge subscription not found',
        )
        _wait_until(
            lambda: self.stop_publisher.get_subscription_count() >= 1,
            message='stop bridge subscription not found',
        )
        _wait_until(
            lambda: (
                self.bridge._cmd_vel_publisher.get_subscription_count() >= 1
            ),
            message='/cmd_vel test subscription not found',
        )

    def close(self):
        self.executor.shutdown()
        self.spin_thread.join(timeout=2.0)
        if self.action_server is not None:
            self.action_server.destroy()
        self.bridge.destroy_node()
        self.publisher_node.destroy_node()
        if self.server_node is not None:
            self.server_node.destroy_node()
        rclpy.shutdown()


class _ManualFuture:
    def __init__(self):
        self._callbacks = []
        self._result = None
        self._error = None
        self._done = False

    def add_done_callback(self, callback):
        if self._done:
            callback(self)
        else:
            self._callbacks.append(callback)

    def result(self):
        if self._error is not None:
            raise self._error
        return self._result

    def set_result(self, result):
        self._result = result
        self._done = True
        for callback in list(self._callbacks):
            callback(self)

    def set_exception(self, error):
        self._error = error
        self._done = True
        for callback in list(self._callbacks):
            callback(self)


class _ManualGoalHandle:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.result_future = _ManualFuture()
        self.cancel_future = _ManualFuture()
        self.cancel_calls = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancel_calls += 1
        return self.cancel_future


class _ImmediateCancelGoalHandle(_ManualGoalHandle):
    def cancel_goal_async(self):
        self.cancel_calls += 1
        future = _ManualFuture()
        response = type(
            'CancelResponse',
            (),
            {'goals_canceling': [object()]},
        )()
        future.set_result(response)
        return future


class _CancelFailingGoalHandle(_ManualGoalHandle):
    def cancel_goal_async(self):
        self.cancel_calls += 1
        raise RuntimeError('synthetic cancel failure')


class _ManualActionClient:
    def __init__(self):
        self.send_future = _ManualFuture()
        self.sent_goal = None

    def wait_for_server(self, timeout_sec):
        del timeout_sec
        return True

    def send_goal_async(self, goal):
        self.sent_goal = goal
        return self.send_future

    def destroy(self):
        pass


class _FailingActionClient:
    def wait_for_server(self, timeout_sec):
        del timeout_sec
        raise RuntimeError('synthetic action client failure')

    def destroy(self):
        pass


class _SendFailingActionClient(_ManualActionClient):
    def send_goal_async(self, goal):
        del goal
        raise RuntimeError('synthetic send_goal_async failure')


class _CapturingPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(copy.deepcopy(message))


def test_public_state_set_is_exactly_the_four_documented_states():
    assert {state.value for state in BridgeState} == {
        'IDLE',
        'NAVIGATING',
        'CANCELING',
        'COOLDOWN',
    }


def test_non_finite_duration_parameters_fall_back_to_defaults():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    try:
        _set_parameters(
            bridge,
            server_wait_timeout=float('nan'),
            cooldown_sec=float('inf'),
            zero_twist_interval_sec=float('nan'),
        )
        assert bridge._nonnegative_parameter(
            'server_wait_timeout',
            2.0,
        ) == 2.0
        assert bridge._nonnegative_parameter('cooldown_sec', 0.8) == 0.8
        assert bridge._positive_parameter(
            'zero_twist_interval_sec',
            0.1,
        ) == 0.1
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_action_server_absent_enters_cooldown_then_recovers():
    harness = _RosHarness()
    try:
        _set_parameters(
            harness.bridge,
            server_wait_timeout=0.05,
            cooldown_sec=0.15,
        )
        harness.goal_publisher.publish(_goal())
        _wait_until(
            lambda: harness.bridge.state == BridgeState.COOLDOWN.value,
            message='missing action server did not enter cooldown',
        )
        _wait_until(
            lambda: harness.bridge.state == BridgeState.IDLE.value,
            message='bridge did not recover from missing action server',
        )
    finally:
        harness.close()


def test_action_client_exception_does_not_escape_goal_callback():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    bridge._action_client = _FailingActionClient()
    real_action_client.destroy()
    try:
        bridge._on_goal_pose(_goal())
        assert bridge.state == BridgeState.COOLDOWN.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_send_goal_async_exception_enters_cooldown():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    bridge._action_client = _SendFailingActionClient()
    real_action_client.destroy()
    try:
        bridge._on_goal_pose(_goal())
        assert bridge.state == BridgeState.COOLDOWN.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_send_future_exception_enters_cooldown():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        bridge._on_goal_pose(_goal())
        fake_action_client.send_future.set_exception(
            RuntimeError('synthetic send future failure')
        )
        assert bridge.state == BridgeState.COOLDOWN.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_rejected_goal_enters_cooldown():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        bridge._on_goal_pose(_goal())
        fake_action_client.send_future.set_result(
            _ManualGoalHandle(accepted=False)
        )
        assert bridge.state == BridgeState.COOLDOWN.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_stop_while_goal_response_is_pending_cancels_once_after_acceptance():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        _set_parameters(bridge, ignore_goals_while_active=False)
        bridge._on_goal_pose(_goal())
        assert bridge.state == BridgeState.NAVIGATING.value
        assert bridge._goal_response_pending
        bridge._on_goal_pose(_goal(9.0, 9.0))
        assert fake_action_client.sent_goal.pose.pose.position.x == 2.5

        bridge._on_stop_navigation(Empty())
        bridge._on_stop_navigation(Empty())
        assert bridge.state == BridgeState.CANCELING.value

        goal_handle = _ManualGoalHandle()
        fake_action_client.send_future.set_result(goal_handle)
        assert goal_handle.cancel_calls == 1
        assert not bridge._goal_response_pending
        assert bridge.state == BridgeState.CANCELING.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_invalid_goal_is_ignored_and_valid_quaternion_is_normalized():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        empty_frame = _goal()
        empty_frame.header.frame_id = ''
        bridge._on_goal_pose(empty_frame)
        assert bridge.state == BridgeState.IDLE.value
        assert fake_action_client.sent_goal is None

        non_finite = _goal()
        non_finite.pose.position.x = float('nan')
        bridge._on_goal_pose(non_finite)
        assert bridge.state == BridgeState.IDLE.value
        assert fake_action_client.sent_goal is None

        zero_quaternion = _goal()
        zero_quaternion.pose.orientation.w = 0.0
        bridge._on_goal_pose(zero_quaternion)
        assert bridge.state == BridgeState.IDLE.value
        assert fake_action_client.sent_goal is None

        valid = _goal()
        valid.pose.orientation.w = 2.0
        bridge._on_goal_pose(valid)
        assert bridge.state == BridgeState.NAVIGATING.value
        assert fake_action_client.sent_goal.pose.pose.orientation.w == 1.0
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_invalid_goal_does_not_break_cooldown_bypass_timer():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    try:
        _set_parameters(bridge, ignore_goals_while_active=False)
        bridge._generation = 1
        bridge._state = BridgeState.COOLDOWN
        bridge._schedule_cooldown_timer(1, 10.0)
        cooldown_timer = bridge._cooldown_timer

        invalid = _goal()
        invalid.header.frame_id = ''
        bridge._on_goal_pose(invalid)

        assert bridge.state == BridgeState.COOLDOWN.value
        assert bridge._cooldown_timer is cooldown_timer
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_ignore_false_bypasses_cooldown_once_without_queueing():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        _set_parameters(bridge, ignore_goals_while_active=False)
        bridge._generation = 1
        bridge._state = BridgeState.COOLDOWN
        bridge._schedule_cooldown_timer(1, 10.0)

        bridge._on_goal_pose(_goal(4.0, 5.0))

        assert bridge.state == BridgeState.NAVIGATING.value
        assert bridge._generation == 2
        assert bridge._cooldown_timer is None
        assert fake_action_client.sent_goal.pose.pose.position.x == 4.0

        bridge._on_goal_pose(_goal(8.0, 9.0))
        assert bridge._generation == 2
        assert fake_action_client.sent_goal.pose.pose.position.x == 4.0
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_shutdown_cancels_once_and_finishes_exact_zero_twist_count():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    capturing_publisher = _CapturingPublisher()
    bridge._cmd_vel_publisher = capturing_publisher
    node_destroyed = False
    try:
        _set_parameters(bridge, zero_twist_repeats=4)
        bridge._on_goal_pose(_goal())
        goal_handle = _ManualGoalHandle()
        fake_action_client.send_future.set_result(goal_handle)

        bridge.destroy_node()
        node_destroyed = True

        assert goal_handle.cancel_calls == 1
        assert len(capturing_publisher.messages) == 4
    finally:
        if not node_destroyed:
            bridge.destroy_node()
        rclpy.shutdown()


def test_shutdown_waits_for_pending_goal_response_and_observes_cancel():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    timer = None
    try:
        bridge._on_goal_pose(_goal())
        goal_handle = _ImmediateCancelGoalHandle()
        timer = threading.Timer(
            0.02,
            lambda: fake_action_client.send_future.set_result(goal_handle),
        )
        timer.start()

        observed = bridge.shutdown_active_task(timeout_sec=0.3)

        assert observed
        assert goal_handle.cancel_calls == 1
        assert bridge._cancel_response_observed
    finally:
        if timer is not None:
            timer.join(timeout=1.0)
        bridge.destroy_node()
        rclpy.shutdown()


def test_cancel_exception_waits_for_the_active_action_result():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        bridge._on_goal_pose(_goal())
        goal_handle = _CancelFailingGoalHandle()
        fake_action_client.send_future.set_result(goal_handle)
        assert bridge.state == BridgeState.NAVIGATING.value

        bridge._on_stop_navigation(Empty())
        assert goal_handle.cancel_calls == 1
        assert bridge.state == BridgeState.CANCELING.value
        assert bridge._goal_handle is goal_handle

        wrapped_result = type(
            'WrappedResult',
            (),
            {'status': GoalStatus.STATUS_ABORTED},
        )()
        goal_handle.result_future.set_result(wrapped_result)
        assert bridge.state == BridgeState.COOLDOWN.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_result_future_exception_runs_cancel_then_cooldown():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        bridge._on_goal_pose(_goal())
        goal_handle = _ManualGoalHandle()
        fake_action_client.send_future.set_result(goal_handle)

        goal_handle.result_future.set_exception(
            RuntimeError('synthetic result future failure')
        )
        assert bridge.state == BridgeState.CANCELING.value
        assert goal_handle.cancel_calls == 1

        cancel_response = type(
            'CancelResponse',
            (),
            {'goals_canceling': [object()]},
        )()
        goal_handle.cancel_future.set_result(cancel_response)
        assert bridge.state == BridgeState.COOLDOWN.value
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_aborted_result_completes_cooldown_and_returns_idle():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    real_action_client = bridge._action_client
    fake_action_client = _ManualActionClient()
    bridge._action_client = fake_action_client
    real_action_client.destroy()
    try:
        _set_parameters(bridge, cooldown_sec=0.0)
        bridge._on_goal_pose(_goal())
        goal_handle = _ManualGoalHandle()
        fake_action_client.send_future.set_result(goal_handle)
        wrapped_result = type(
            'WrappedResult',
            (),
            {'status': GoalStatus.STATUS_ABORTED},
        )()

        goal_handle.result_future.set_result(wrapped_result)

        assert bridge.state == BridgeState.IDLE.value
        assert bridge._goal_handle is None
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


def test_duplicate_goal_and_stop_are_ignored_and_stop_is_finite():
    requests = []
    cancel_requests = []
    release_execution = threading.Event()

    def execute_callback(goal_handle):
        requests.append(copy.deepcopy(goal_handle.request.pose))
        while (
            not goal_handle.is_cancel_requested
            and not release_execution.is_set()
        ):
            time.sleep(0.01)
        if goal_handle.is_cancel_requested:
            goal_handle.canceled()
        else:
            goal_handle.abort()
        return NavigateToPose.Result()

    def cancel_callback(goal_handle):
        del goal_handle
        cancel_requests.append(time.monotonic())
        return CancelResponse.ACCEPT

    harness = _RosHarness(execute_callback, cancel_callback)
    try:
        _set_parameters(
            harness.bridge,
            cooldown_sec=0.4,
            zero_twist_repeats=3,
            zero_twist_interval_sec=0.05,
        )
        original = _goal(stamp_sec=123)
        harness.goal_publisher.publish(original)
        _wait_until(
            lambda: len(requests) == 1,
            message='goal was not forwarded',
        )
        _wait_until(
            lambda: harness.bridge.state == BridgeState.NAVIGATING.value,
            message='bridge did not enter NAVIGATING',
        )

        assert original.header.stamp.sec == 123
        assert requests[0].header.frame_id == 'map'
        assert requests[0].header.stamp.sec != 123
        assert requests[0].pose.position.x == 2.5
        assert requests[0].pose.position.y == -1.25

        for index in range(5):
            harness.goal_publisher.publish(
                _goal(4.0 + index, 3.0 - index)
            )
        time.sleep(0.15)
        assert len(requests) == 1

        harness.stop_publisher.publish(Empty())
        harness.stop_publisher.publish(Empty())
        _wait_until(
            lambda: len(cancel_requests) == 1,
            message='active goal was not canceled',
        )
        _wait_until(
            lambda: harness.bridge.state == BridgeState.COOLDOWN.value,
            message='canceled goal did not enter cooldown',
        )
        harness.goal_publisher.publish(_goal(7.0, 8.0))
        _wait_until(
            lambda: len(harness.zero_twists) == 3,
            message='finite zero Twist burst was not published',
        )
        time.sleep(0.15)
        assert len(cancel_requests) == 1
        assert len(harness.zero_twists) == 3
        assert all(message.linear.x == 0.0 for message in harness.zero_twists)
        assert all(message.linear.y == 0.0 for message in harness.zero_twists)
        assert all(message.angular.z == 0.0 for message in harness.zero_twists)

        assert len(requests) == 1
        _wait_until(
            lambda: harness.bridge.state == BridgeState.IDLE.value,
            message='bridge did not leave cooldown',
        )
    finally:
        release_execution.set()
        harness.close()


def test_goal_after_cooldown_is_forwarded():
    requests = []

    def execute_callback(goal_handle):
        requests.append(copy.deepcopy(goal_handle.request.pose))
        goal_handle.succeed()
        return NavigateToPose.Result()

    harness = _RosHarness(execute_callback)
    try:
        _set_parameters(harness.bridge, cooldown_sec=0.2)
        harness.goal_publisher.publish(_goal(1.0, 2.0))
        _wait_until(lambda: len(requests) == 1)
        _wait_until(
            lambda: harness.bridge.state == BridgeState.COOLDOWN.value,
        )

        harness.goal_publisher.publish(_goal(3.0, 4.0))
        time.sleep(0.1)
        assert len(requests) == 1

        _wait_until(
            lambda: harness.bridge.state == BridgeState.IDLE.value,
        )
        harness.goal_publisher.publish(_goal(5.0, 6.0))
        _wait_until(lambda: len(requests) == 2)
        assert requests[1].pose.position.x == 5.0
        assert requests[1].pose.position.y == 6.0
    finally:
        harness.close()


def test_stale_async_callback_cannot_change_current_generation():
    rclpy.init()
    bridge = GoalPoseActionBridge()
    try:
        current_future = object()
        bridge._generation = 2
        bridge._state = BridgeState.NAVIGATING
        current_send_future = object()
        bridge._result_future = current_future
        current_cancel_future = object()
        bridge._send_future = current_send_future
        bridge._cancel_future = current_cancel_future

        bridge._on_goal_response(object(), generation=1)
        bridge._on_goal_result(object(), generation=1)
        bridge._on_cancel_response(object(), generation=1)

        assert bridge.state == BridgeState.NAVIGATING.value
        assert bridge._send_future is current_send_future
        assert bridge._result_future is current_future
        assert bridge._cancel_future is current_cancel_future
    finally:
        bridge.destroy_node()
        rclpy.shutdown()

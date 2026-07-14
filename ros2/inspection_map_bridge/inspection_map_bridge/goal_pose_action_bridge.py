"""Forward frontend navigation commands to the Nav2 action safely."""

import copy
import math
import time
from enum import Enum

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy
from rclpy.qos import QoSHistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import QoSReliabilityPolicy
from std_msgs.msg import Empty


class BridgeState(Enum):
    """Single-task states used by the goal bridge."""

    IDLE = 'IDLE'
    NAVIGATING = 'NAVIGATING'
    CANCELING = 'CANCELING'
    COOLDOWN = 'COOLDOWN'


class GoalPoseActionBridge(Node):
    """Translate frontend goal and stop topics into one Nav2 action task."""

    def __init__(self, **kwargs):
        super().__init__(
            'inspection_map_goal_pose_action_bridge',
            **kwargs
        )
        self.declare_parameter('server_wait_timeout', 2.0)
        self.declare_parameter('cooldown_sec', 0.8)
        self.declare_parameter('zero_twist_repeats', 3)
        self.declare_parameter('zero_twist_interval_sec', 0.1)
        self.declare_parameter('ignore_goals_while_active', True)

        qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        self._action_client = ActionClient(
            self,
            NavigateToPose,
            '/navigate_to_pose',
        )
        self._goal_subscription = self.create_subscription(
            PoseStamped,
            '/inspection_map/goal_pose',
            self._on_goal_pose,
            qos,
        )
        self._stop_subscription = self.create_subscription(
            Empty,
            '/inspection_map/stop_navigation',
            self._on_stop_navigation,
            qos,
        )
        self._cmd_vel_publisher = self.create_publisher(
            Twist,
            '/cmd_vel',
            qos,
        )

        self._state = BridgeState.IDLE
        self._generation = 0
        self._goal_handle = None
        self._send_future = None
        self._result_future = None
        self._cancel_future = None
        self._goal_response_pending = False
        self._cancel_requested = False
        self._cancel_dispatched = False
        self._cancel_response_observed = False
        self._shutdown_started = False
        self._cooldown_timer = None
        self._zero_twist_timer = None
        self._zero_twist_generation = 0
        self._zero_twists_remaining = 0

        self.get_logger().info(
            (
                '{} Goal bridge ready: /inspection_map/goal_pose -> '
                '/navigate_to_pose; '
                'stop topic is /inspection_map/stop_navigation.'
            ).format(self._log_context())
        )

    @property
    def state(self):
        """Return the current state label for diagnostics and tests."""
        return self._state.value

    def destroy_node(self):
        """Best-effort stop an active task, then destroy ROS entities."""
        self.shutdown_active_task(timeout_sec=0.0)
        self._destroy_named_timer('_cooldown_timer')
        self._destroy_named_timer('_zero_twist_timer')
        self._action_client.destroy()
        return super().destroy_node()

    def shutdown_active_task(self, timeout_sec=0.5):
        """Request cancellation and observe it for a bounded shutdown window."""
        if self._state not in (BridgeState.NAVIGATING, BridgeState.CANCELING):
            return True

        generation = self._generation
        if not self._shutdown_started:
            self._shutdown_started = True
            self._state = BridgeState.CANCELING
            self._cancel_requested = True
            self.get_logger().warning(
                '{} Node shutdown requested; issuing a bounded best-effort '
                'stop.'.format(self._log_context(generation))
            )
            self._finish_zero_twist_burst_now(generation)
            self._request_cancel(generation)

        try:
            timeout = max(0.0, float(timeout_sec))
        except (TypeError, ValueError, OverflowError):
            timeout = 0.5
        if not math.isfinite(timeout):
            timeout = 0.5

        deadline = time.monotonic() + timeout
        while rclpy.ok() and time.monotonic() < deadline:
            if self._shutdown_cancel_observed():
                self.get_logger().info(
                    '{} Shutdown observed cancellation progress.'.format(
                        self._log_context(generation)
                    )
                )
                return True
            remaining = deadline - time.monotonic()
            rclpy.spin_once(self, timeout_sec=min(0.05, remaining))

        observed = self._shutdown_cancel_observed()
        if not observed and timeout > 0.0:
            self.get_logger().warning(
                '{} Shutdown cancel was not observed within {:.3f} seconds; '
                'destroying the node after the finite zero-Twist pulse.'.format(
                    self._log_context(generation),
                    timeout,
                )
            )
        return observed

    def _shutdown_cancel_observed(self):
        return (
            self._state in (BridgeState.COOLDOWN, BridgeState.IDLE)
            or self._cancel_response_observed
        )

    def _on_goal_pose(self, pose):
        if self._state != BridgeState.IDLE:
            if (
                self._state == BridgeState.COOLDOWN
                and not self._ignore_goals_while_active()
                and self._zero_twists_remaining <= 0
            ):
                prepared_pose = self._prepare_goal_pose(pose)
                if prepared_pose is None:
                    return
                self._destroy_named_timer('_cooldown_timer')
                self._state = BridgeState.IDLE
                self.get_logger().warning(
                    '{} Cooldown bypassed because '
                    'ignore_goals_while_active is false.'.format(
                        self._log_context()
                    )
                )
                self._start_goal(prepared_pose)
                return

            self.get_logger().warning(
                '{} Ignoring /inspection_map/goal_pose: bridge is not '
                'IDLE.'.format(self._log_context())
            )
            return

        prepared_pose = self._prepare_goal_pose(pose)
        if prepared_pose is not None:
            self._start_goal(prepared_pose)

    def _start_goal(self, pose):
        self._generation += 1
        generation = self._generation
        self._state = BridgeState.NAVIGATING
        self._goal_handle = None
        self._send_future = None
        self._result_future = None
        self._cancel_future = None
        self._goal_response_pending = True
        self._cancel_requested = False
        self._cancel_dispatched = False
        self._cancel_response_observed = False
        self._shutdown_started = False
        self.get_logger().info(
            '{} Accepted /inspection_map/goal_pose; waiting for the Nav2 '
            'goal response.'.format(
                self._log_context(generation)
            )
        )

        timeout = self._nonnegative_parameter('server_wait_timeout', 2.0)
        try:
            server_ready = self._action_client.wait_for_server(
                timeout_sec=timeout
            )
        except Exception as error:
            self.get_logger().error(
                '{} NavigateToPose server check failed: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            self._enter_cooldown(generation)
            return

        if not server_ready:
            self.get_logger().error(
                '{} NavigateToPose action server was not available within '
                '{:.1f} seconds; goal was not sent.'.format(
                    self._log_context(generation),
                    timeout,
                )
            )
            self._enter_cooldown(generation)
            return

        goal = NavigateToPose.Goal()
        goal.pose = copy.deepcopy(pose)
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.behavior_tree = ''

        try:
            send_future = self._action_client.send_goal_async(goal)
            self._send_future = send_future
            send_future.add_done_callback(
                lambda future, token=generation: self._on_goal_response(
                    future,
                    token,
                )
            )
        except Exception as error:
            self._send_future = None
            self._goal_response_pending = False
            self.get_logger().error(
                '{} NavigateToPose send_goal failed: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            self._enter_cooldown(generation)
            return

        self.get_logger().info(
            '{} Sending NavigateToPose goal in frame {} to '
            'x={:.3f}, y={:.3f}'.format(
                self._log_context(generation),
                goal.pose.header.frame_id or 'map',
                goal.pose.pose.position.x,
                goal.pose.pose.position.y,
            )
        )

    def _on_goal_response(self, future, generation):
        if not self._is_current_future(future, generation, '_send_future'):
            self.get_logger().warning(
                '{} Ignoring stale goal response for sequence {}.'.format(
                    self._log_context(),
                    generation,
                )
            )
            return
        self._send_future = None
        self._goal_response_pending = False

        try:
            goal_handle = future.result()
        except Exception as error:
            self.get_logger().error(
                '{} NavigateToPose send_goal response failed: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            self._enter_cooldown(generation)
            return

        if goal_handle is None or not goal_handle.accepted:
            self.get_logger().warning(
                '{} NavigateToPose goal was rejected by Nav2.'.format(
                    self._log_context(generation)
                )
            )
            self._enter_cooldown(generation)
            return

        self._goal_handle = goal_handle
        if self._cancel_requested:
            self._state = BridgeState.CANCELING
        else:
            self._state = BridgeState.NAVIGATING
        self.get_logger().info(
            '{} NavigateToPose goal was accepted by Nav2.'.format(
                self._log_context(generation)
            )
        )

        try:
            result_future = goal_handle.get_result_async()
            self._result_future = result_future
            result_future.add_done_callback(
                lambda result, token=generation: self._on_goal_result(
                    result,
                    token,
                )
            )
        except Exception as error:
            self._result_future = None
            self.get_logger().error(
                '{} Could not monitor NavigateToPose result: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            self._state = BridgeState.CANCELING
            self._cancel_requested = True
            self._start_zero_twist_burst(generation)

        if self._cancel_requested:
            self._request_cancel(generation)

    def _on_goal_result(self, future, generation):
        if not self._is_current_future(future, generation, '_result_future'):
            self.get_logger().warning(
                '{} Ignoring stale goal result for sequence {}.'.format(
                    self._log_context(),
                    generation,
                )
            )
            return
        self._result_future = None

        try:
            wrapped_result = future.result()
        except Exception as error:
            self.get_logger().error(
                '{} NavigateToPose result failed: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            self._state = BridgeState.CANCELING
            self._cancel_requested = True
            self._start_zero_twist_burst(generation)
            if self._cancel_dispatched:
                if self._cancel_future is None:
                    self._enter_cooldown(generation)
            else:
                self._request_cancel(generation)
            return

        status = wrapped_result.status
        labels = {
            GoalStatus.STATUS_SUCCEEDED: 'succeeded',
            GoalStatus.STATUS_CANCELED: 'canceled',
            GoalStatus.STATUS_ABORTED: 'aborted',
        }
        self.get_logger().info(
            '{} NavigateToPose finished with status {} ({}).'.format(
                self._log_context(generation),
                status,
                labels.get(status, 'other'),
            )
        )
        self._enter_cooldown(generation)

    def _on_stop_navigation(self, message):
        del message
        if self._state not in (
            BridgeState.NAVIGATING,
        ):
            self.get_logger().warning(
                '{} Ignoring stop: no cancellable task is active.'.format(
                    self._log_context()
                )
            )
            return

        generation = self._generation
        self._state = BridgeState.CANCELING
        self._cancel_requested = True
        self.get_logger().info(
            '{} Stop accepted; canceling the active NavigateToPose goal '
            'once. goal_response_pending={}'.format(
                self._log_context(generation),
                self._goal_response_pending,
            )
        )
        self._start_zero_twist_burst(generation)
        self._request_cancel(generation)

    def _request_cancel(self, generation):
        if generation != self._generation:
            return
        if self._cancel_dispatched:
            return
        if self._goal_handle is None:
            if self._cancel_requested:
                self.get_logger().info(
                    '{} Cancel is pending until Nav2 returns a goal '
                    'handle.'.format(self._log_context(generation))
                )
            return

        self._cancel_dispatched = True
        self.get_logger().info(
            '{} Dispatching one NavigateToPose cancel request.'.format(
                self._log_context(generation)
            )
        )
        try:
            cancel_future = self._goal_handle.cancel_goal_async()
            self._cancel_future = cancel_future
            cancel_future.add_done_callback(
                lambda result, token=generation: self._on_cancel_response(
                    result,
                    token,
                )
            )
        except Exception as error:
            self._cancel_future = None
            self.get_logger().error(
                '{} NavigateToPose cancel request failed: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            if self._result_future is None:
                self._enter_cooldown(generation)
            else:
                self.get_logger().warning(
                    '{} Keeping the bridge in CANCELING until the active '
                    'action result is known.'.format(
                        self._log_context(generation)
                    )
                )

    def _on_cancel_response(self, future, generation):
        if not self._is_current_future(future, generation, '_cancel_future'):
            self.get_logger().warning(
                '{} Ignoring stale cancel response for sequence {}.'.format(
                    self._log_context(),
                    generation,
                )
            )
            return
        self._cancel_future = None
        self._cancel_response_observed = True

        try:
            response = future.result()
            canceling_count = len(response.goals_canceling)
        except Exception as error:
            self.get_logger().error(
                '{} NavigateToPose cancel response failed: {}'.format(
                    self._log_context(generation),
                    error,
                )
            )
            canceling_count = 0

        if canceling_count:
            self.get_logger().info(
                '{} Nav2 accepted the cancellation request.'.format(
                    self._log_context(generation)
                )
            )
        else:
            self.get_logger().warning(
                '{} Nav2 did not report an action goal being canceled.'.format(
                    self._log_context(generation)
                )
            )

        if self._result_future is None:
            self._enter_cooldown(generation)

    def _start_zero_twist_burst(self, generation):
        if self._zero_twist_generation == generation:
            return

        self._destroy_named_timer('_zero_twist_timer')
        self._zero_twist_generation = generation
        repeat_count = self._zero_twist_repeat_count()
        self._zero_twists_remaining = repeat_count
        self.get_logger().info(
            '{} Publishing {} finite zero Twist message(s).'.format(
                self._log_context(generation),
                repeat_count,
            )
        )
        self._publish_next_zero_twist(generation)

    def _finish_zero_twist_burst_now(self, generation):
        if self._zero_twist_generation == generation:
            repeat_count = self._zero_twists_remaining
        else:
            self._zero_twist_generation = generation
            repeat_count = self._zero_twist_repeat_count()

        self._destroy_named_timer('_zero_twist_timer')
        self._zero_twists_remaining = 0
        self.get_logger().info(
            '{} Publishing {} remaining shutdown zero Twist message(s).'.format(
                self._log_context(generation),
                repeat_count,
            )
        )
        for _ in range(repeat_count):
            self._cmd_vel_publisher.publish(Twist())

    def _publish_next_zero_twist(self, generation):
        if generation != self._zero_twist_generation:
            return
        if self._zero_twists_remaining <= 0:
            self._destroy_named_timer('_zero_twist_timer')
            return

        self._cmd_vel_publisher.publish(Twist())
        self._zero_twists_remaining -= 1
        if self._zero_twists_remaining <= 0:
            self._destroy_named_timer('_zero_twist_timer')
            return

        if self._zero_twist_timer is None:
            interval = self._positive_parameter(
                'zero_twist_interval_sec',
                0.1,
            )
            self._zero_twist_timer = self.create_timer(
                interval,
                lambda token=generation: self._publish_next_zero_twist(token),
            )

    def _enter_cooldown(self, generation):
        if generation != self._generation:
            return
        if self._state == BridgeState.COOLDOWN:
            return

        self._state = BridgeState.COOLDOWN
        self._goal_handle = None
        self._send_future = None
        self._result_future = None
        self._cancel_future = None
        self._goal_response_pending = False
        self._cancel_requested = False
        self._cancel_dispatched = False

        cooldown = self._nonnegative_parameter('cooldown_sec', 0.8)
        self.get_logger().info(
            '{} Cooldown started for {:.3f} seconds.'.format(
                self._log_context(generation),
                cooldown,
            )
        )
        self._schedule_cooldown_timer(generation, cooldown)

    def _schedule_cooldown_timer(self, generation, delay):
        self._destroy_named_timer('_cooldown_timer')
        if delay <= 0.0 and self._zero_twists_remaining <= 0:
            self._finish_cooldown(generation)
            return

        if delay <= 0.0:
            delay = self._positive_parameter(
                'zero_twist_interval_sec',
                0.1,
            )
        self._cooldown_timer = self.create_timer(
            delay,
            lambda token=generation: self._on_cooldown_timer(token),
        )

    def _on_cooldown_timer(self, generation):
        self._destroy_named_timer('_cooldown_timer')
        if generation != self._generation:
            return
        if self._state != BridgeState.COOLDOWN:
            return
        if self._zero_twists_remaining > 0:
            self._schedule_cooldown_timer(
                generation,
                self._positive_parameter('zero_twist_interval_sec', 0.1),
            )
            return
        self._finish_cooldown(generation)

    def _finish_cooldown(self, generation):
        if generation != self._generation:
            return
        self._state = BridgeState.IDLE
        self._zero_twist_generation = 0
        self.get_logger().info(
            '{} Cooldown ended; bridge is IDLE.'.format(
                self._log_context(generation)
            )
        )

    def _is_current_future(self, future, generation, attribute_name):
        return (
            generation == self._generation
            and future is getattr(self, attribute_name)
        )

    def _ignore_goals_while_active(self):
        return bool(
            self.get_parameter('ignore_goals_while_active').value
        )

    def _log_context(self, generation=None):
        if generation is None:
            generation = self._generation
        return '[sequence={} state={}]'.format(
            generation,
            self._state.value,
        )

    def _nonnegative_parameter(self, name, default):
        try:
            value = float(self.get_parameter(name).value)
        except (OverflowError, TypeError, ValueError):
            value = float('nan')
        if math.isfinite(value) and value >= 0.0:
            return value
        self.get_logger().warning(
            'Invalid {} parameter; using {}.'.format(name, default)
        )
        return float(default)

    def _positive_parameter(self, name, default):
        value = self._nonnegative_parameter(name, default)
        if value > 0.0:
            return value
        self.get_logger().warning(
            '{} must be positive; using {}.'.format(name, default)
        )
        return float(default)

    def _positive_int_parameter(self, name, default):
        try:
            value = int(self.get_parameter(name).value)
        except (OverflowError, TypeError, ValueError):
            value = int(default)
        if value >= 1:
            return value
        self.get_logger().warning(
            '{} must be at least 1; using {}.'.format(name, default)
        )
        return int(default)

    def _zero_twist_repeat_count(self):
        return self._positive_int_parameter('zero_twist_repeats', 3)

    def _prepare_goal_pose(self, pose):
        frame_id = pose.header.frame_id
        if (
            not frame_id
            or frame_id != frame_id.strip()
            or '\x00' in frame_id
        ):
            self.get_logger().error(
                '{} Ignoring /inspection_map/goal_pose: invalid or empty '
                'frame_id.'.format(
                    self._log_context()
                )
            )
            return None

        position = pose.pose.position
        orientation = pose.pose.orientation
        values = (
            position.x,
            position.y,
            position.z,
            orientation.x,
            orientation.y,
            orientation.z,
            orientation.w,
        )
        try:
            values_are_finite = all(
                math.isfinite(float(value)) for value in values
            )
        except (TypeError, ValueError):
            values_are_finite = False
        if not values_are_finite:
            self.get_logger().error(
                '{} Ignoring /inspection_map/goal_pose: pose contains a '
                'non-finite value.'.format(self._log_context())
            )
            return None

        quaternion_norm = math.hypot(
            orientation.x,
            orientation.y,
            orientation.z,
            orientation.w,
        )
        if not math.isfinite(quaternion_norm) or quaternion_norm <= 1.0e-9:
            self.get_logger().error(
                '{} Ignoring /inspection_map/goal_pose: quaternion length is '
                'zero.'.format(
                    self._log_context()
                )
            )
            return None

        prepared_pose = copy.deepcopy(pose)
        prepared_orientation = prepared_pose.pose.orientation
        prepared_orientation.x /= quaternion_norm
        prepared_orientation.y /= quaternion_norm
        prepared_orientation.z /= quaternion_norm
        prepared_orientation.w /= quaternion_norm
        return prepared_pose

    def _destroy_named_timer(self, attribute_name):
        timer = getattr(self, attribute_name, None)
        if timer is None:
            return
        setattr(self, attribute_name, None)
        try:
            timer.cancel()
        except Exception:
            pass
        try:
            self.destroy_timer(timer)
        except Exception:
            pass


def main(args=None):
    """Run the goal-to-action bridge."""
    rclpy.init(args=args)
    node = GoalPoseActionBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown_active_task(timeout_sec=0.5)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

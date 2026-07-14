"""Publish realistic offline navigation data for the inspection map UI."""

import math
import time
import uuid

import rclpy
from action_msgs.msg import GoalInfo
from action_msgs.msg import GoalStatus
from action_msgs.msg import GoalStatusArray
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import Point32
from geometry_msgs.msg import Pose
from geometry_msgs.msg import PoseArray
from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import PoseWithCovarianceStamped
from geometry_msgs.msg import TransformStamped
from geometry_msgs.msg import Twist
from nav_msgs.msg import OccupancyGrid
from nav_msgs.msg import Path
from nav2_msgs.action import NavigateToPose
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy
from rclpy.qos import QoSHistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import QoSReliabilityPolicy
from sensor_msgs.msg import LaserScan
from sensor_msgs.msg import PointCloud
from std_msgs.msg import Empty
from std_msgs.msg import Header
from tf2_msgs.msg import TFMessage

from inspection_map_bridge.simulation import interpolate_path
from inspection_map_bridge.simulation import make_costmap_data
from inspection_map_bridge.simulation import quaternion_to_yaw
from inspection_map_bridge.simulation import yaw_to_quaternion


try:
    from nav2_msgs.action._navigate_to_pose import (
        NavigateToPose_FeedbackMessage,
    )
except ImportError:
    NavigateToPose_FeedbackMessage = NavigateToPose.Impl.FeedbackMessage


class InspectionMapTestDataPublisher(Node):
    """Provide all dynamic layers expected by the Flutter inspection map."""

    def __init__(self):
        super().__init__('inspection_map_test_data')

        reliable_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        sensor_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        transient_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )

        self._scan_publisher = self.create_publisher(
            LaserScan, '/scan', sensor_qos
        )
        self._amcl_publisher = self.create_publisher(
            PoseWithCovarianceStamped, '/amcl_pose', reliable_qos
        )
        self._plan_publisher = self.create_publisher(Path, '/plan', reliable_qos)
        self._local_plan_publisher = self.create_publisher(
            Path, '/local_plan', reliable_qos
        )
        self._global_costmap_publisher = self.create_publisher(
            OccupancyGrid, '/global_costmap/costmap', transient_qos
        )
        self._local_costmap_publisher = self.create_publisher(
            OccupancyGrid, '/local_costmap/costmap', transient_qos
        )
        self._particle_publisher = self.create_publisher(
            PoseArray, '/particlecloud', reliable_qos
        )
        self._cost_cloud_publisher = self.create_publisher(
            PointCloud, '/cost_cloud', reliable_qos
        )
        self._tf_publisher = self.create_publisher(TFMessage, '/tf', sensor_qos)
        self._tf_static_publisher = self.create_publisher(
            TFMessage, '/tf_static', transient_qos
        )
        self._status_publisher = self.create_publisher(
            GoalStatusArray,
            '/navigate_to_pose/_action/status',
            reliable_qos,
        )
        self._feedback_publisher = self.create_publisher(
            NavigateToPose_FeedbackMessage,
            '/navigate_to_pose/_action/feedback',
            reliable_qos,
        )
        self._cmd_vel_publisher = self.create_publisher(
            Twist, '/cmd_vel', reliable_qos
        )

        self.create_subscription(
            PoseWithCovarianceStamped,
            '/initialpose',
            self._on_initial_pose,
            reliable_qos,
        )
        self.create_subscription(
            PoseStamped,
            '/inspection_map/goal_pose',
            self._on_goal_pose,
            reliable_qos,
        )
        self.create_subscription(
            Empty,
            '/inspection_map/stop_navigation',
            self._on_stop_navigation,
            reliable_qos,
        )
        self.create_service(
            CancelGoal,
            '/navigate_to_pose/_action/cancel_goal',
            self._on_cancel_goal,
        )

        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._demo_targets = [(3.0, 1.5), (-2.5, 2.5), (1.0, -3.0)]
        self._demo_target_index = 0
        self._demo_mode = True
        self._goal_x = self._demo_targets[0][0]
        self._goal_y = self._demo_targets[0][1]
        self._goal_yaw = 0.0
        self._linear_speed = 0.35
        self._angular_speed = 0.0
        self._status = GoalStatus.STATUS_EXECUTING
        self._goal_id = self._make_uuid()
        self._goal_stamp = self.get_clock().now().to_msg()
        self._navigation_started = time.monotonic()
        self._accepted_until = 0.0
        self._zero_velocity_pending = False
        self._phase = 0.0

        self._publish_static_tf()
        self.create_timer(0.1, self._publish_fast_data)
        self.create_timer(0.5, self._publish_status)
        self.create_timer(1.0, self._publish_slow_data)

        self.get_logger().info(
            'Offline inspection map data is active; listening on '
            '/initialpose, /inspection_map/goal_pose, '
            '/inspection_map/stop_navigation, '
            'and the NavigateToPose cancel service.'
        )

    @staticmethod
    def _make_uuid():
        return list(uuid.uuid4().bytes)

    def _header(self, frame_id):
        header = Header()
        header.stamp = self.get_clock().now().to_msg()
        header.frame_id = frame_id
        return header

    @staticmethod
    def _set_pose(target, x_value, y_value, yaw):
        target.position.x = float(x_value)
        target.position.y = float(y_value)
        target.position.z = 0.0
        quaternion = yaw_to_quaternion(yaw)
        target.orientation.x = quaternion[0]
        target.orientation.y = quaternion[1]
        target.orientation.z = quaternion[2]
        target.orientation.w = quaternion[3]

    def _pose_stamped(self, x_value, y_value, yaw, frame_id='map'):
        message = PoseStamped()
        message.header = self._header(frame_id)
        self._set_pose(message.pose, x_value, y_value, yaw)
        return message

    def _on_initial_pose(self, message):
        pose = message.pose.pose
        self._x = float(pose.position.x)
        self._y = float(pose.position.y)
        self._yaw = quaternion_to_yaw(
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        )
        self.get_logger().info(
            'Accepted /initialpose at x={:.2f}, y={:.2f}, yaw={:.2f}'.format(
                self._x, self._y, self._yaw
            )
        )

    def _on_goal_pose(self, message):
        pose = message.pose
        self._goal_x = float(pose.position.x)
        self._goal_y = float(pose.position.y)
        self._goal_yaw = quaternion_to_yaw(
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        )
        self._goal_id = self._make_uuid()
        self._goal_stamp = self.get_clock().now().to_msg()
        self._navigation_started = time.monotonic()
        self._accepted_until = self._navigation_started + 0.25
        self._status = GoalStatus.STATUS_ACCEPTED
        self._demo_mode = False
        self._zero_velocity_pending = False
        self._publish_status()
        self.get_logger().info(
            'Accepted /inspection_map/goal_pose at '
            'x={:.2f}, y={:.2f}, yaw={:.2f}'.format(
                self._goal_x, self._goal_y, self._goal_yaw
            )
        )

    def _on_cancel_goal(self, request, response):
        del request
        goal_info = self._current_goal_info()
        response.return_code = 0
        if self._cancel_navigation():
            response.goals_canceling = [goal_info]
            self.get_logger().info(
                'NavigateToPose cancellation accepted; one zero Twist '
                'will be sent.'
            )
        else:
            response.goals_canceling = []
            self.get_logger().info(
                'NavigateToPose cancellation ignored; no goal is active.'
            )
        return response

    def _on_stop_navigation(self, message):
        del message
        if self._cancel_navigation():
            self.get_logger().info(
                'Offline stop accepted; one zero Twist will be sent.'
            )
        else:
            self.get_logger().info(
                'Offline stop ignored; no navigation goal is active.'
            )

    def _cancel_navigation(self):
        if self._status not in (
            GoalStatus.STATUS_ACCEPTED,
            GoalStatus.STATUS_EXECUTING,
        ):
            return False

        self._status = GoalStatus.STATUS_CANCELED
        self._demo_mode = False
        self._linear_speed = 0.0
        self._angular_speed = 0.0
        self._zero_velocity_pending = True
        self._publish_status()
        return True

    def _current_goal_info(self):
        goal_info = GoalInfo()
        goal_info.goal_id.uuid = list(self._goal_id)
        goal_info.stamp = self._goal_stamp
        return goal_info

    def _update_motion(self):
        now = time.monotonic()
        if self._status == GoalStatus.STATUS_ACCEPTED:
            if now < self._accepted_until:
                return
            self._status = GoalStatus.STATUS_EXECUTING

        if self._status != GoalStatus.STATUS_EXECUTING:
            return

        delta_x = self._goal_x - self._x
        delta_y = self._goal_y - self._y
        distance = math.hypot(delta_x, delta_y)
        if distance <= 0.06:
            self._x = self._goal_x
            self._y = self._goal_y
            self._yaw = self._goal_yaw
            if self._demo_mode:
                self._advance_demo_goal()
                return
            self._linear_speed = 0.0
            self._angular_speed = 0.0
            self._status = GoalStatus.STATUS_SUCCEEDED
            self._zero_velocity_pending = True
            self._publish_status()
            return

        desired_yaw = math.atan2(delta_y, delta_x)
        previous_yaw = self._yaw
        self._yaw = desired_yaw
        step = min(0.035, distance)
        self._x += step * math.cos(self._yaw)
        self._y += step * math.sin(self._yaw)
        self._linear_speed = step / 0.1
        self._angular_speed = (self._yaw - previous_yaw) / 0.1

    def _advance_demo_goal(self):
        self._demo_target_index = (
            self._demo_target_index + 1
        ) % len(self._demo_targets)
        target = self._demo_targets[self._demo_target_index]
        self._goal_x = target[0]
        self._goal_y = target[1]
        self._goal_yaw = math.atan2(self._goal_y - self._y, self._goal_x - self._x)
        self._goal_id = self._make_uuid()
        self._goal_stamp = self.get_clock().now().to_msg()
        self._navigation_started = time.monotonic()
        self._status = GoalStatus.STATUS_EXECUTING

    def _publish_fast_data(self):
        self._phase += 0.1
        self._update_motion()
        self._publish_tf()
        self._publish_amcl_pose()
        self._publish_scan()
        self._publish_feedback()

        if self._status == GoalStatus.STATUS_EXECUTING:
            self._publish_velocity(self._linear_speed, self._angular_speed)
        elif self._zero_velocity_pending:
            self._publish_velocity(0.0, 0.0)
            self._zero_velocity_pending = False

    def _publish_static_tf(self):
        transform = TransformStamped()
        transform.header = self._header('base_link')
        transform.child_frame_id = 'laser'
        transform.transform.translation.x = 0.17
        transform.transform.translation.y = 0.0
        transform.transform.translation.z = 0.12
        transform.transform.rotation.w = 1.0
        message = TFMessage()
        message.transforms = [transform]
        self._tf_static_publisher.publish(message)

    def _publish_tf(self):
        map_to_odom = TransformStamped()
        map_to_odom.header = self._header('map')
        map_to_odom.child_frame_id = 'odom'
        map_to_odom.transform.rotation.w = 1.0

        odom_to_base = TransformStamped()
        odom_to_base.header = self._header('odom')
        odom_to_base.child_frame_id = 'base_link'
        odom_to_base.transform.translation.x = self._x
        odom_to_base.transform.translation.y = self._y
        quaternion = yaw_to_quaternion(self._yaw)
        odom_to_base.transform.rotation.x = quaternion[0]
        odom_to_base.transform.rotation.y = quaternion[1]
        odom_to_base.transform.rotation.z = quaternion[2]
        odom_to_base.transform.rotation.w = quaternion[3]

        message = TFMessage()
        message.transforms = [map_to_odom, odom_to_base]
        self._tf_publisher.publish(message)

    def _publish_amcl_pose(self):
        message = PoseWithCovarianceStamped()
        message.header = self._header('map')
        self._set_pose(message.pose.pose, self._x, self._y, self._yaw)
        covariance = [0.0] * 36
        covariance[0] = 0.04
        covariance[7] = 0.04
        covariance[35] = 0.02
        message.pose.covariance = covariance
        self._amcl_publisher.publish(message)

    def _publish_scan(self):
        message = LaserScan()
        message.header = self._header('laser')
        message.angle_min = -math.pi
        sample_count = 360
        message.angle_increment = 2.0 * math.pi / sample_count
        message.angle_max = (
            message.angle_min + (sample_count - 1) * message.angle_increment
        )
        message.time_increment = 0.0
        message.scan_time = 0.1
        message.range_min = 0.12
        message.range_max = 8.0
        ranges = []
        for index in range(sample_count):
            local_angle = message.angle_min + index * message.angle_increment
            ranges.append(self._room_range(local_angle))
        message.ranges = ranges
        message.intensities = [80.0] * sample_count
        self._scan_publisher.publish(message)

    def _room_range(self, local_angle):
        world_angle = self._yaw + local_angle
        direction_x = math.cos(world_angle)
        direction_y = math.sin(world_angle)
        candidates = []
        for boundary in (-5.0, 5.0):
            if abs(direction_x) > 1.0e-6:
                distance = (boundary - self._x) / direction_x
                hit_y = self._y + distance * direction_y
                if distance > 0.0 and -5.0 <= hit_y <= 5.0:
                    candidates.append(distance)
            if abs(direction_y) > 1.0e-6:
                distance = (boundary - self._y) / direction_y
                hit_x = self._x + distance * direction_x
                if distance > 0.0 and -5.0 <= hit_x <= 5.0:
                    candidates.append(distance)

        if not candidates:
            return 8.0
        ripple = 0.015 * math.sin(local_angle * 7.0 + self._phase)
        return max(0.12, min(8.0, min(candidates) + ripple))

    def _publish_velocity(self, linear, angular):
        message = Twist()
        message.linear.x = float(linear)
        message.angular.z = float(angular)
        self._cmd_vel_publisher.publish(message)

    def _publish_status(self):
        status = GoalStatus()
        status.goal_info = self._current_goal_info()
        status.status = int(self._status)
        message = GoalStatusArray()
        message.status_list = [status]
        self._status_publisher.publish(message)

    def _publish_feedback(self):
        message = NavigateToPose_FeedbackMessage()
        message.goal_id.uuid = list(self._goal_id)
        message.feedback.current_pose = self._pose_stamped(
            self._x, self._y, self._yaw
        )
        elapsed = max(0.0, time.monotonic() - self._navigation_started)
        distance = math.hypot(self._goal_x - self._x, self._goal_y - self._y)
        estimate = distance / 0.35 if self._status == GoalStatus.STATUS_EXECUTING else 0.0
        self._set_duration(message.feedback.navigation_time, elapsed)
        if hasattr(message.feedback, 'estimated_time_remaining'):
            self._set_duration(
                message.feedback.estimated_time_remaining,
                estimate,
            )
        message.feedback.number_of_recoveries = 0
        message.feedback.distance_remaining = float(distance)
        self._feedback_publisher.publish(message)

    @staticmethod
    def _set_duration(target, seconds):
        whole_seconds = int(seconds)
        target.sec = whole_seconds
        target.nanosec = int((seconds - whole_seconds) * 1000000000.0)

    def _publish_slow_data(self):
        self._publish_paths()
        self._publish_costmaps()
        self._publish_particles()
        self._publish_cost_cloud()

    def _publish_paths(self):
        points = interpolate_path(
            self._x,
            self._y,
            self._goal_x,
            self._goal_y,
            40,
        )
        global_path = Path()
        global_path.header = self._header('map')
        global_path.poses = [
            self._pose_stamped(x_value, y_value, self._yaw)
            for x_value, y_value in points
        ]
        self._plan_publisher.publish(global_path)

        local_path = Path()
        local_path.header = self._header('map')
        local_path.poses = global_path.poses[:12]
        self._local_plan_publisher.publish(local_path)

    def _make_costmap(self, width, height, resolution, origin_x, origin_y, phase):
        message = OccupancyGrid()
        message.header = self._header('map')
        message.info.map_load_time = self.get_clock().now().to_msg()
        message.info.resolution = float(resolution)
        message.info.width = int(width)
        message.info.height = int(height)
        self._set_pose(message.info.origin, origin_x, origin_y, 0.0)
        message.data = make_costmap_data(width, height, phase)
        return message

    def _publish_costmaps(self):
        global_costmap = self._make_costmap(
            120, 120, 0.1, -6.0, -6.0, int(self._phase)
        )
        self._global_costmap_publisher.publish(global_costmap)

        local_costmap = self._make_costmap(
            50,
            50,
            0.1,
            self._x - 2.5,
            self._y - 2.5,
            int(self._phase * 2.0),
        )
        self._local_costmap_publisher.publish(local_costmap)

    def _publish_particles(self):
        message = PoseArray()
        message.header = self._header('map')
        poses = []
        for index in range(32):
            angle = index * 2.0 * math.pi / 32.0
            radius = 0.08 + 0.025 * (index % 4)
            pose = Pose()
            self._set_pose(
                pose,
                self._x + radius * math.cos(angle),
                self._y + radius * math.sin(angle),
                self._yaw + 0.08 * math.sin(angle),
            )
            poses.append(pose)
        message.poses = poses
        self._particle_publisher.publish(message)

    def _publish_cost_cloud(self):
        message = PointCloud()
        message.header = self._header('map')
        points = []
        for index in range(48):
            angle = index * 2.0 * math.pi / 48.0
            radius = 1.1 + 0.25 * math.sin(index * 0.7 + self._phase)
            point = Point32()
            point.x = float(self._x + radius * math.cos(angle))
            point.y = float(self._y + radius * math.sin(angle))
            point.z = 0.0
            points.append(point)
        message.points = points
        message.channels = []
        self._cost_cloud_publisher.publish(message)


def main(args=None):
    """Run the offline test-data publisher."""
    rclpy.init(args=args)
    node = InspectionMapTestDataPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

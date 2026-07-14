"""Launch the standalone inspection map ROS/rosbridge stack."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import OpaqueFunction
from launch.actions import SetEnvironmentVariable
from launch.actions import TimerAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def _launch_flag(context, name):
    value = LaunchConfiguration(name).perform(context).strip().lower()
    return value in ('1', 'true', 'yes', 'on')


def _validate_navigation_mode(context):
    if _launch_flag(context, 'test_data') and _launch_flag(
        context,
        'goal_action_bridge',
    ):
        raise RuntimeError(
            'test_data and goal_action_bridge cannot both be true: the '
            'offline publisher and real Nav2 bridge must not share '
            '/inspection_map/goal_pose '
            'or /cmd_vel.'
        )
    return []


def generate_launch_description():
    """Build the inspection map launch description."""
    package_share = get_package_share_directory('inspection_map_bridge')

    default_map = os.path.join(package_share, 'maps', 'yahboomcar.yaml')
    default_fastdds_profile = os.path.join(
        package_share,
        'config',
        'fastdds_udp_only.xml',
    )

    map_yaml = LaunchConfiguration('map_yaml')
    fastdds_profiles_file = LaunchConfiguration('fastdds_profiles_file')
    rosbridge_port = LaunchConfiguration('rosbridge_port')
    rosbridge_address = LaunchConfiguration('rosbridge_address')
    domain_id = LaunchConfiguration('domain_id')
    test_data = LaunchConfiguration('test_data')
    goal_action_bridge = LaunchConfiguration('goal_action_bridge')
    server_wait_timeout = LaunchConfiguration('server_wait_timeout')
    cooldown_sec = LaunchConfiguration('cooldown_sec')
    zero_twist_repeats = LaunchConfiguration('zero_twist_repeats')
    zero_twist_interval_sec = LaunchConfiguration(
        'zero_twist_interval_sec'
    )
    ignore_goals_while_active = LaunchConfiguration(
        'ignore_goals_while_active'
    )
    start_map_server = LaunchConfiguration('start_map_server')
    use_sim_time = LaunchConfiguration('use_sim_time')

    map_server = Node(
        package='nav2_map_server',
        executable='map_server',
        name='map_server',
        output='screen',
        condition=IfCondition(start_map_server),
        parameters=[
            {
                'yaml_filename': map_yaml,
                'use_sim_time': ParameterValue(use_sim_time, value_type=bool),
            },
        ],
    )

    lifecycle_manager = Node(
        package='nav2_lifecycle_manager',
        executable='lifecycle_manager',
        name='inspection_map_lifecycle_manager',
        output='screen',
        condition=IfCondition(start_map_server),
        parameters=[
            {
                'autostart': True,
                'node_names': ['map_server'],
                'use_sim_time': ParameterValue(use_sim_time, value_type=bool),
            },
        ],
    )

    rosbridge = Node(
        package='inspection_map_bridge',
        executable='rosbridge_websocket_compat',
        name='rosbridge_websocket',
        output='screen',
        parameters=[
            {
                'port': ParameterValue(rosbridge_port, value_type=int),
                'address': rosbridge_address,
                'max_message_size': 50000000,
            },
        ],
    )

    rosapi = Node(
        package='rosapi',
        executable='rosapi_node',
        name='rosapi',
        output='screen',
    )

    offline_data = Node(
        package='inspection_map_bridge',
        executable='test_data_publisher',
        name='inspection_map_test_data',
        output='screen',
        condition=IfCondition(test_data),
        parameters=[
            {
                'use_sim_time': ParameterValue(use_sim_time, value_type=bool),
            },
        ],
    )

    goal_bridge = Node(
        package='inspection_map_bridge',
        executable='goal_pose_action_bridge',
        name='inspection_map_goal_pose_action_bridge',
        output='screen',
        condition=IfCondition(goal_action_bridge),
        parameters=[
            {
                'use_sim_time': ParameterValue(use_sim_time, value_type=bool),
                'server_wait_timeout': ParameterValue(
                    server_wait_timeout,
                    value_type=float,
                ),
                'cooldown_sec': ParameterValue(
                    cooldown_sec,
                    value_type=float,
                ),
                'zero_twist_repeats': ParameterValue(
                    zero_twist_repeats,
                    value_type=int,
                ),
                'zero_twist_interval_sec': ParameterValue(
                    zero_twist_interval_sec,
                    value_type=float,
                ),
                'ignore_goals_while_active': ParameterValue(
                    ignore_goals_while_active,
                    value_type=bool,
                ),
            },
        ],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'map_yaml',
                default_value=default_map,
                description='Absolute path to a map_server YAML file.',
            ),
            DeclareLaunchArgument(
                'fastdds_profiles_file',
                default_value=default_fastdds_profile,
                description=(
                    'Fast DDS XML profile inherited by launched ROS nodes.'
                ),
            ),
            DeclareLaunchArgument(
                'rosbridge_port',
                default_value='9090',
                description='rosbridge WebSocket TCP port.',
            ),
            DeclareLaunchArgument(
                'rosbridge_address',
                default_value='0.0.0.0',
                description='Address on which rosbridge accepts WebSockets.',
            ),
            DeclareLaunchArgument(
                'domain_id',
                default_value='30',
                description='ROS_DOMAIN_ID inherited by all launched nodes.',
            ),
            DeclareLaunchArgument(
                'test_data',
                default_value='false',
                description=(
                    'Publish offline scan, localization, and Nav2 data.'
                ),
            ),
            DeclareLaunchArgument(
                'goal_action_bridge',
                default_value='false',
                description=(
                    'Forward /inspection_map/goal_pose to the Nav2 '
                    'NavigateToPose action.'
                ),
            ),
            DeclareLaunchArgument(
                'server_wait_timeout',
                default_value='2.0',
                description='Seconds to wait for the NavigateToPose server.',
            ),
            DeclareLaunchArgument(
                'cooldown_sec',
                default_value='0.8',
                description='Minimum delay after a terminal navigation event.',
            ),
            DeclareLaunchArgument(
                'zero_twist_repeats',
                default_value='3',
                description='Finite zero Twist count for an accepted stop.',
            ),
            DeclareLaunchArgument(
                'zero_twist_interval_sec',
                default_value='0.1',
                description='Interval between stop zero Twist messages.',
            ),
            DeclareLaunchArgument(
                'ignore_goals_while_active',
                default_value='true',
                description='Ignore new goals until the active task is idle.',
            ),
            DeclareLaunchArgument(
                'start_map_server',
                default_value='true',
                description=(
                    'Start and lifecycle-manage the standalone map server.'
                ),
            ),
            DeclareLaunchArgument(
                'use_sim_time',
                default_value='false',
                description='Use a /clock source instead of wall time.',
            ),
            SetEnvironmentVariable('ROS_DOMAIN_ID', domain_id),
            SetEnvironmentVariable(
                'FASTRTPS_DEFAULT_PROFILES_FILE',
                fastdds_profiles_file,
            ),
            OpaqueFunction(function=_validate_navigation_mode),
            map_server,
            lifecycle_manager,
            offline_data,
            goal_bridge,
            TimerAction(period=1.5, actions=[rosbridge, rosapi]),
        ]
    )

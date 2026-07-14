# inspection_map_bridge

This ROS 2 Foxy ament_python package is the independent ROS side of the
Flutter inspection map. It does not import, start, or modify
/root/Control_demo/app_control_gateway.py.

The launch starts:

- nav2_map_server/map_server for the real OccupancyGrid on /map
- nav2_lifecycle_manager so map_server becomes active automatically
- rosbridge_server/rosbridge_websocket on port 9090 by default
- a local Foxy compatibility shim that lets rosbridge load generated
  NavigateToPose feedback topic messages from `nav2_msgs.action`
- an optional offline node that publishes scan, pose, path, costmap, TF, and
  navigation action telemetry
- an optional goal bridge that converts frontend
  /inspection_map/goal_pose messages into
  real Nav2 /navigate_to_pose action goals and owns stop/cancel handling

All child processes inherit ROS_DOMAIN_ID=30 unless domain_id is overridden.
They also inherit the installed `config/fastdds_udp_only.xml` profile. This
keeps ROS user data on UDPv4 when Fast DDS discovery works but shared-memory
delivery is broken inside an older Docker container.

## Required map files

Copy the Yahboom map pair into `maps` before building:

    maps/yahboomcar.yaml
    maps/yahboomcar.pgm

Keep the PGM bytes unchanged. In the copied YAML, replace the original
robot-specific absolute image path with a path relative to the YAML file:

    image: yahboomcar.pgm

## Ubuntu 20.04 / Foxy dependencies

    source /opt/ros/foxy/setup.bash
    sudo apt update
    sudo apt install -y \
      python3-colcon-common-extensions \
      ros-foxy-nav2-map-server \
      ros-foxy-nav2-lifecycle-manager \
      ros-foxy-rosbridge-server \
      ros-foxy-nav2-msgs

## Build and test

Run from the inspection_map_ws directory:

    source /opt/ros/foxy/setup.bash
    rm -rf build install log
    colcon build --symlink-install
    source install/setup.bash
    colcon test --packages-select inspection_map_bridge
    colcon test-result --verbose

The goal bridge tests cover an unavailable or failing action client, frontend
timestamp replacement, duplicate goals, stop while a goal is still being
accepted by Nav2, cancel-once behavior, the finite zero-Twist burst, cooldown
rejection, and stale asynchronous callbacks.

## Navigation command state machine

The real-robot bridge accepts commands on:

    /inspection_map/goal_pose          geometry_msgs/msg/PoseStamped
    /inspection_map/stop_navigation   std_msgs/msg/Empty

It keeps one task in `IDLE`, `NAVIGATING`, `CANCELING`, or `COOLDOWN`.

```text
IDLE --accepted goal--> NAVIGATING --Stop--> CANCELING
  ^                         |                    |
  |                         | success/abort/     | canceled/error
  |                         | reject/error       |
  +------ cooldown timer -- COOLDOWN <-----------+
```

`NAVIGATING` includes the short internal phase while the bridge is waiting for
Nav2 to accept or reject the action goal. With the default configuration, any
goal received outside `IDLE` and any stop received outside `NAVIGATING` is
ignored. One accepted stop cancels the current action handle once and
publishes a finite burst of zero `Twist` messages. Old action futures carry a
task generation token and cannot change the state of a newer task.

If Stop arrives while the goal response is pending inside `NAVIGATING`, before
Nav2 has returned a goal handle, the bridge records that Stop as pending. If
Nav2 then accepts the goal, the bridge requests cancellation exactly once as
soon as the handle exists. A second Stop in `CANCELING` is ignored.

The bridge replaces every frontend goal header timestamp with the ROS node's
current clock immediately before calling `send_goal_async`. It rejects empty
frames, non-finite pose values, and zero-length quaternions, then normalizes a
valid quaternion before forwarding it. Parameters and defaults are:

    server_wait_timeout:=2.0
    cooldown_sec:=0.8
    zero_twist_repeats:=3
    zero_twist_interval_sec:=0.1
    ignore_goals_while_active:=true

Goals are never queued or used to replace an active task. With
`ignore_goals_while_active:=false`, a goal may only bypass `COOLDOWN`, after
the previous action handle has been cleared and any zero-Twist burst has
finished. It still cannot replace a goal in `NAVIGATING` or `CANCELING`.

`zero_twist_repeats` must be a positive integer and the bridge publishes that
exact finite count. Keep the production value in the intended 1-3 range.
If the process is shut down during an active task, it dispatches cancellation
at most once, synchronously finishes the configured or remaining zero-Twist
count, and waits up to 0.5 seconds for the pending goal response, cancel
response, or terminal transition before destroying the action client. A timeout
is logged because a process cannot guarantee delivery after its DDS entities
have been destroyed.

Operational logs include the task sequence and state for command acceptance
or rejection, server and action errors, cancel request/result, terminal action
status, and cooldown start/end. This makes interleaved old action callbacks
distinguishable from the current task.

## Offline launch

    source /opt/ros/foxy/setup.bash
    source install/setup.bash
    ros2 launch inspection_map_bridge inspection_map.launch.py \
      domain_id:=30 rosbridge_port:=9090 test_data:=true

`test_data` now defaults to `false`, so offline simulation must be selected
explicitly. `test_data:=true` and `goal_action_bridge:=true` are mutually
exclusive; launch fails before starting nodes if both are requested because
both modes consume `/inspection_map/goal_pose` and can publish `/cmd_vel`.

For an offline bridge-only check without Nav2, use a short server timeout:

    ros2 launch inspection_map_bridge inspection_map.launch.py --show-args
    ros2 launch inspection_map_bridge inspection_map.launch.py \
      test_data:=false goal_action_bridge:=true \
      start_map_server:=false server_wait_timeout:=0.2

In another terminal, verify the command subscriptions and publish one test
goal. The bridge should log that the action server is unavailable, enter
cooldown, and remain alive:

    ros2 node info /inspection_map_goal_pose_action_bridge
    ros2 topic info /inspection_map/goal_pose
    ros2 topic info /inspection_map/stop_navigation
    ros2 topic pub -1 /inspection_map/goal_pose geometry_msgs/msg/PoseStamped \
      "{header: {frame_id: map}, pose: {orientation: {w: 1.0}}}"
    ros2 topic pub -1 /inspection_map/stop_navigation std_msgs/msg/Empty '{}'

The pytest action server is the repeatable offline verification for accepted
goals, duplicate commands, cancel, cooldown, zero velocity, and timestamp
rewriting. These checks do not constitute real-robot validation.

For the configured Ubuntu 20.04 VMware guest, use a clean workspace and an
isolated local-only ROS domain:

    source /opt/ros/foxy/setup.bash
    export ROS_DOMAIN_ID=31
    export ROS_LOCALHOST_ONLY=1
    cd ~/inspection_map_ws_single_goal
    colcon build --symlink-install --packages-select inspection_map_bridge
    source install/setup.bash
    colcon test --packages-select inspection_map_bridge \
      --event-handlers console_direct+
    colcon test-result --verbose
    ros2 launch inspection_map_bridge inspection_map.launch.py --show-args
    python3 -m pytest -vv -s \
      src/inspection_map_bridge/test/test_goal_pose_action_bridge.py

Label every result from this procedure `VMware ROS2 Foxy offline validation`.
It is not evidence of Yahboom X3 deployment, localization, or physical motion.

Connect Flutter Web to:

    ws://127.0.0.1:9090

The map publisher is transient-local. The Foxy rosbridge implementation
detects the map publisher QoS and creates a
transient-local subscription, so a browser connecting after map_server has
published still receives /map. rosbridge is delayed briefly by the launch so
the map publisher is visible before browser subscriptions are created.

The compatibility executable does not replace rosbridge. It installs one
loader fallback before running the packaged `rosbridge_websocket`: Foxy action
packages keep `NavigateToPose_FeedbackMessage` in a generated private action
module even though the graph type is
`nav2_msgs/action/NavigateToPose_FeedbackMessage`. Ordinary message/service
loading remains unchanged.

In another terminal, remember that the launch environment does not change the
calling shell:

    source /opt/ros/foxy/setup.bash
    source install/setup.bash
    export ROS_DOMAIN_ID=30
    ros2 topic hz /map
    ros2 topic hz /scan
    ros2 topic info --verbose /map

For a direct transient-local check:

    ros2 topic echo /map nav_msgs/msg/OccupancyGrid \
      --qos-durability transient_local --qos-reliability reliable

## Frontend message checks

The offline node subscribes to the exact frontend types:

    /initialpose                       geometry_msgs/msg/PoseWithCovarianceStamped
    /inspection_map/goal_pose           geometry_msgs/msg/PoseStamped
    /inspection_map/stop_navigation    std_msgs/msg/Empty

It also provides:

    /navigate_to_pose/_action/cancel_goal  action_msgs/srv/CancelGoal

An all-zero goal UUID or one Empty Stop cancels the current simulated goal.
Repeated Stop messages are ignored after the first cancellation. Cancellation
sends one zero Twist; it does not continuously publish zero velocity.

## Real Yahboom X3 deployment

Copy this package source into a clean Foxy workspace on the car, copy the
Yahboom YAML/PGM pair into maps, build it, then run without simulated data:

    source /opt/ros/foxy/setup.bash
    cd ~/inspection_map_ws
    colcon build --symlink-install
    source install/setup.bash
    export ROS_DOMAIN_ID=30
    export FASTRTPS_DEFAULT_PROFILES_FILE=/root/inspection_map_ws/install/inspection_map_bridge/share/inspection_map_bridge/config/fastdds_udp_only.xml
    ros2 launch inspection_map_bridge inspection_map.launch.py \
      domain_id:=30 rosbridge_port:=9090 test_data:=false \
      goal_action_bridge:=true server_wait_timeout:=2.0 \
      cooldown_sec:=0.8 zero_twist_repeats:=3 \
      zero_twist_interval_sec:=0.1 ignore_goals_while_active:=true

If an already-running Nav2 launch owns map_server, avoid a duplicate node and
use its /map instead:

    ros2 launch inspection_map_bridge inspection_map.launch.py \
      domain_id:=30 rosbridge_port:=9090 test_data:=false \
      goal_action_bridge:=true start_map_server:=false \
      server_wait_timeout:=2.0 cooldown_sec:=0.8 \
      zero_twist_repeats:=3 zero_twist_interval_sec:=0.1 \
      ignore_goals_while_active:=true

The goal bridge is disabled in the offline launch because the offline data
node already consumes `/inspection_map/goal_pose` and simulates the action
topics. On the real car it must be enabled. Yahboom's `bt_navigator` also
subscribes to Nav2's native `/goal_pose`, so the frontend must use the isolated
`/inspection_map/goal_pose` command topic. The frontend must publish one
`std_msgs/msg/Empty` message on `/inspection_map/stop_navigation`; the bridge
is the sole cancellation owner for goals that it forwards.

For a remote browser, replace 127.0.0.1 with the car address and allow TCP
9090 through the firewall. Do not use the Windows Node/SSH bridge as the
navigation transport.

Start the Yahboom hardware and Nav2 with the same DDS environment. For the
vendor aliases this means exporting the variables before running `n1` and
`n3`; otherwise nodes can be visible in `ros2 node list` while `/scan`,
`/odom`, `/tf`, and `/map` carry no user data.

Before bringing up navigation, a finite zero-Twist pulse can be used as a
separate startup safety check:

    timeout 3s ros2 topic pub -r 10 \
      /cmd_vel geometry_msgs/msg/Twist '{}'

Do not leave that publisher running, because Nav2 must regain sole velocity
control after the safety pulse. Normal UI stops do not need this command; the
goal bridge emits its configured finite zero-Twist burst.

The live command probe is deliberately armed only with explicit confirmation
and a goal no farther than 0.20 m from the supplied current pose:

    dart tool/rosbridge_command_probe.dart \
      --live --confirm-live=ROBOT_AREA_CLEAR \
      --host=<robot-ip> --port=9090 \
      --current-x=<map-x> --current-y=<map-y> --current-yaw=<radians> \
      --goal-x=<map-x> --goal-y=<map-y> --goal-yaw=<radians>

It waits for action status and feedback, then publishes one Empty message on
`/inspection_map/stop_navigation` in every post-goal cleanup path. The bridge,
not the probe, owns action cancellation and the finite zero-Twist burst.

## Stop

Press Ctrl+C in the launch terminal to stop the stack. In real-robot mode, one
message on `/inspection_map/stop_navigation` moves the bridge to `CANCELING`,
cancels the active action handle once, and sends the configured finite zero
`Twist` burst. Repeated stop messages during `CANCELING` or `COOLDOWN` are
ignored. The offline test-data node continues to use its simulated
NavigateToPose CancelGoal service and also accepts the same Empty Stop topic.
It is never launched together with the real goal bridge.

The goal timestamp fix does not alter `/initialpose`. The browser currently
publishes that message directly, so a robot-side initial-pose relay that
replaces its timestamp is still recommended if browser and robot clocks cannot
be kept synchronized.

## Rollback

Before deployment, copy the existing robot package to the Windows backup
directory described by the project backup procedure. To restore a confirmed
archive later, copy it only to the robot host `/tmp`, copy it into the
`elegant_buck` container, extract it under `/root`, and rebuild only this
package:

    docker cp /tmp/<backup>.tgz elegant_buck:/tmp/<backup>.tgz
    docker exec elegant_buck sh -lc \
      'cd /root && tar -xzf /tmp/<backup>.tgz'
    docker exec elegant_buck sh -lc \
      'source /opt/ros/foxy/setup.bash && \
       source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash && \
       cd /root/inspection_map_ws && \
       colcon build --symlink-install \
         --packages-select inspection_map_bridge'

After verifying the restored build, delete both host and container copies
from `/tmp`. Do not extract the archive into or modify the Yahboom vendor
workspace.

## Known real-robot limits

This state machine prevents duplicate frontend commands from creating
overlapping action tasks. It does not repair missing `map` or `odom` TF,
uninitialized AMCL, stale DDS user data, inactive Nav2 lifecycle nodes, a
missing controller `/cmd_vel` publisher, or a native Nav2 crash. Those must be
checked on the robot after the hardware, `n1`, and `n3` are online in one DDS
environment.

## Troubleshooting

- No /map: confirm map_server is active with ros2 lifecycle get /map_server,
  inspect the YAML image path, and inspect /map publisher QoS.
- WebSocket works but no topics: confirm every terminal and container uses
  ROS_DOMAIN_ID=30 and the same `FASTRTPS_DEFAULT_PROFILES_FILE`. Verify the
  DDS data plane with a real subscriber; discovery alone is insufficient.
- No /scan on the real car: inspect USB enumeration and recreate any container
  whose /dev/bus/usb bind points at an obsolete device number.
- Nav2 reports a missing map frame: verify /map is publishing and that /tf and
  /tf_static provide a connected map to odom to base_link to laser tree.
- AMCL waits for localization: publish /initialpose from the Flutter page or
  with ros2 topic pub using PoseWithCovarianceStamped.

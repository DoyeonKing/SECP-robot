# Runtime Environment

This document records the audited runtime context. It is not a startup script and no robot program was executed while creating the snapshot.

## Robot host

- Python: `3.8.10`
- Docker Server: `24.0.0`
- `patrol_ai` entry point: `/home/jetson/patrol_ai/patrol_ai_runner.py`
- External runtime dependencies include OpenCV, NumPy, InsightFace, YOLOv5 code, and a model weight file. External model and identity data are not included.
- Sanitized local configuration variables:
  - `PATROL_ELDER_PROFILE_ID`
  - `PATROL_ELDER_CODE`

The repository template leaves both variables empty. Real values must be supplied outside Git.

## gateway container

- Container: `icar_foxy_new`
- Image: `icar/ros-foxy:1.0.2`
- Python: `3.8.10`
- ROS distribution: `foxy`
- ROS domain ID: `32`
- Runtime process: `python3 /root/Control_demo/app_control_gateway.py`

Configuration variable names observed in the application:

- `APP_CONTROL_USE_TCP_CONTROL`
- `APP_CONTROL_PUBLISH_CMD_VEL`
- `APP_CONTROL_TCP_HOST`
- `APP_CONTROL_TCP_PORT`
- `APP_CONTROL_TCP_SPEED`
- `APP_CONTROL_TCP_TIMEOUT_SEC`
- `APP_CONTROL_HTTP_HOST`
- `APP_CONTROL_HTTP_PORT`
- `SPRINGBOOT_BASE_URL`
- `SPRINGBOOT_HTTP_TIMEOUT_SEC`
- `ENTERTAINMENT_BACKEND_BASE_URL`
- `ENTERTAINMENT_HTTP_TIMEOUT_SEC`
- `ENTERTAINMENT_MUSIC_DIR`
- `ENTERTAINMENT_PLAYER_CMD`
- `ENTERTAINMENT_DANCE_CMD`
- `ENTERTAINMENT_AUDIO_DEVICE`
- `ENTERTAINMENT_POLL_INTERVAL_SEC`
- `SOS_ALARM_POLL_INTERVAL_SEC`

Runtime values were not copied into the snapshot. The running process had a deployment-specific backend URL configured; its value is intentionally withheld.

## inspection bridge container

- Container: `elegant_buck`
- Image: `yahboomtechnology/ros-foxy:5.0.1`
- Python: `3.8.10`
- Package source: `/root/inspection_map_ws/src/inspection_map_bridge`
- ROS build, install, and log directories are not included.
- The occupancy-grid map pair is deployment data and must be supplied outside Git.

## Collection safety

- No patrol, gateway, bridge, ROS control, or navigation program was started or stopped.
- No ROS message or movement command was published.
- Neither container was restarted, stopped, committed, removed, or rebuilt.
- The two containers remained running with unchanged start timestamps and restart counts.

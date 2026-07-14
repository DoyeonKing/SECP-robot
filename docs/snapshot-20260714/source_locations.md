# Source Locations

Snapshot date: 2026-07-14 (Asia/Shanghai)

Collection mode: remote sources were read and copied without changing the robot host or containers. The only source edit described below was made in this local snapshot.

## patrol_ai

- Runtime source root: `10.137.172.125:/home/jetson/patrol_ai`
- Included source files: `fall_detector.py`, `crack_detect_camera.py`, `fall_detect.py`, `patrol_ai_runner.py`, `face_common.py`, and `face_recognizer.py`
- `face_common.py` was dereferenced from `/home/jetson/face_db/scripts/face_common.py` into a normal local file.
- `face_recognizer.py` was dereferenced from `/home/jetson/face_db/scripts/face_recognizer.py` into a normal local file.
- The robot source directory is not a Git repository.

### Authorized local sanitization

- File: `patrol_ai/patrol_ai_runner.py`
- Original robot SHA256: `c35ebee127b65b8ac3a5e98b325f00a71bbed34ad33e54d211f9ce5854f3c04b`
- Original size: 20,737 bytes
- Sanitized snapshot SHA256: `13d3695c385ddf57f4f9501b7aabf9003c255d853c0fd6e2fee932a2b4d7b70b`
- Sanitized size: 20,810 bytes
- Added supporting import: `os`
- Changed field `elderProfileId` to read `PATROL_ELDER_PROFILE_ID` with an empty default.
- Changed field `elderCode` to read `PATROL_ELDER_CODE` with an empty default.
- No identity value is retained in the snapshot or configuration template.
- The robot file was not modified and its SHA256 was rechecked after collection.
- This configuration sanitization is the intentional difference between the robot runtime version and the Git version.

## gateway

- Container: `icar_foxy_new`
- Runtime source: `/root/Control_demo/app_control_gateway.py`
- Included local path: `gateway/app_control_gateway.py`
- Source SHA256: `ef820e6a1d6923150dbb20d04c33b25021343b624955dfb3b4007101a6f1657b`
- The running application does not import a team-added local Python module.
- No existing configuration template was present in the container directory.

## inspection_map_bridge

- Container source: `elegant_buck:/root/inspection_map_ws/src/inspection_map_bridge`
- Verified local mirror: `D:/Desktop/little_semaster/05/deploy/inspection_map_bridge_isolated_goal/inspection_map_bridge`
- All 21 container files matched the local mirror by relative path, byte size, and SHA256.
- Normalized 21-file digest: `5b1b946688217a94a292313f0bfb62e54ae06bfa960a4305a006b85bad1991d5`
- The snapshot was copied from the verified local mirror.
- The site map pair was deliberately excluded, leaving 19 package source and documentation files.

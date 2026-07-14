#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, request
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool, String


class EntertainmentClient:
    def __init__(self, base_url: str, timeout_sec: float = 5.0):
        self.base_url = base_url.rstrip('/')
        self.timeout_sec = timeout_sec

    def is_enabled(self) -> bool:
        return bool(self.base_url)

    def fetch_pending_tasks(self):
        response = self._request_json(
            'GET',
            '/api/entertainment/tasks/pending',
        )
        if not response.get('success', False):
            raise RuntimeError(response.get('message', 'fetch pending tasks failed'))
        return response.get('data') or []

    def update_task_status(self, task_id: int, status: str, message: str):
        self._request_json(
            'PUT',
            f'/api/entertainment/tasks/{task_id}/status',
            {
                'status': status,
                'message': message,
            },
        )

    def fetch_emergency_alert(self, alert_id):
        return self._request_json(
            'GET',
            f'/v1/emergency-alerts/{alert_id}',
        )

    def _request_json(self, method: str, path: str, payload=None):
        if not self.base_url:
            raise RuntimeError('entertainment backend base url is empty')

        url = f'{self.base_url}{path}'
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'

        request_obj = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request_obj, timeout=self.timeout_sec) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            body = response.read().decode(charset)
            return json.loads(body) if body else {}


class InspectionForwardClient:
    def __init__(self, base_url: str, timeout_sec: float = 5.0):
        self.base_url = base_url.rstrip('/')
        self.timeout_sec = timeout_sec

    def is_enabled(self) -> bool:
        return bool(self.base_url)

    def forward_marker(self, payload: dict):
        if not self.base_url:
            raise RuntimeError('springboot base url is empty')

        url = f'{self.base_url}/api/inspection/markers'
        data = json.dumps(payload).encode('utf-8')
        request_obj = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(request_obj, timeout=self.timeout_sec) as response:
            response.read()


class EntertainmentExecutor:
    def __init__(self, music_dir: str, player_command: str, dance_command: str):
        self.music_dir = Path(music_dir)
        self.player_command = player_command.strip()
        self.dance_command = dance_command.strip()
        self.audio_device = os.getenv('ENTERTAINMENT_AUDIO_DEVICE', 'plughw:0,0').strip()

    def execute_task(self, task: dict):
        task_type = str(task.get('taskType', '')).strip().lower()
        if task_type not in {'music', 'dance', 'music_dance'}:
            raise RuntimeError(f'unsupported taskType: {task_type}')

        if task_type == 'music':
            self._play_music(task)
        elif task_type == 'dance':
            self._run_dance(task)
        elif task_type == 'music_dance':
            self._run_dance(task)

    def execute_sos_alarm(self, task: dict, alert_client: EntertainmentClient, alert_id, poll_interval_sec: float = 2.0):
        music_name = str(task.get('musicName') or '').strip()
        music_url = str(task.get('musicUrl') or '').strip()
        local_path = self._resolve_music_path(music_name, music_url)
        if local_path is None:
            raise RuntimeError(f'music file not found for musicName={music_name!r}')

        if local_path.suffix.lower() not in {'.mp3', '.wav'}:
            raise RuntimeError(f'unsupported music file: {local_path.name}')

        wav_path = self._ensure_wav_file(local_path)
        player = None
        last_status = 'sent'

        try:
            while True:
                alert_payload = alert_client.fetch_emergency_alert(alert_id)
                alert_data = alert_payload.get('data', alert_payload) if isinstance(alert_payload, dict) else {}
                if not isinstance(alert_data, dict):
                    raise RuntimeError('invalid emergency alert response')

                status = str(alert_data.get('status') or '').strip().lower()
                if not status:
                    raise RuntimeError('emergency alert status is missing')

                last_status = status
                if status in {'handled', 'cancelled', 'false_alarm'}:
                    break
                if status != 'sent':
                    raise RuntimeError(f'unexpected emergency alert status: {status}')

                if player is None or player.poll() is not None:
                    if player is not None and player.returncode not in (0, None):
                        raise RuntimeError(f'aplay failed with code {player.returncode}')
                    player = subprocess.Popen(
                        ['aplay', '-D', self.audio_device, str(wav_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                    )

                time.sleep(poll_interval_sec)
        finally:
            if player is not None and player.poll() is None:
                player.terminate()
                try:
                    player.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    player.kill()
                    player.wait(timeout=2.0)

        return last_status

    def _play_music(self, task: dict):
        music_name = str(task.get('musicName') or '').strip()
        music_url = str(task.get('musicUrl') or '').strip()
        local_path = self._resolve_music_path(music_name, music_url)
        if local_path is None:
            raise RuntimeError(f'music file not found for musicName={music_name!r}')

        if self.player_command:
            command = self.player_command.format(
                music_name=music_name,
                music_url=music_url,
                music_path=str(local_path),
            )
            self._run_shell_command(command, 'music')
            return

        if local_path.suffix.lower() not in {'.mp3', '.wav'}:
            raise RuntimeError(f'unsupported music file: {local_path.name}')

        wav_path = self._ensure_wav_file(local_path)
        self._play_wav_file(wav_path)

    def _run_dance(self, task: dict):
        task_type = str(task.get('taskType') or '').strip().lower()
        dance_mode = str(task.get('danceMode') or 'default').strip()
        music_name = str(task.get('musicName') or '').strip()
        music_url = str(task.get('musicUrl') or '').strip()
        local_path = None
        dance_music_path = ''

        if music_name or music_url:
            local_path = self._resolve_music_path(music_name, music_url)
            if local_path is None and task_type == 'music_dance':
                raise RuntimeError(f'music file not found for musicName={music_name!r}')

        if local_path is not None:
            if local_path.suffix.lower() not in {'.mp3', '.wav'}:
                raise RuntimeError(f'unsupported music file: {local_path.name}')
            dance_music_path = str(self._ensure_wav_file(local_path))

        if self.dance_command:
            command = self.dance_command.format(
                dance_mode=dance_mode,
                music_name=music_name,
                music_url=music_url,
                music_path=dance_music_path,
            )
            self._run_shell_command(command, 'dance')
            return

        duration_sec = 5.0 if dance_mode == 'gentle' else 8.0
        time.sleep(duration_sec)

    def _resolve_music_path(self, music_name: str, music_url: str):
        candidates = []

        if music_name:
            candidates.extend([
                self.music_dir / music_name,
                self.music_dir / f'{music_name}.mp3',
                self.music_dir / f'{music_name}.wav',
            ])

        if music_url:
            parsed = urllib.parse.urlparse(music_url)
            filename = Path(parsed.path).name
            if filename:
                candidates.append(self.music_dir / filename)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _ensure_wav_file(self, source_path: Path) -> Path:
        if source_path.suffix.lower() == '.wav':
            return source_path

        if source_path.suffix.lower() != '.mp3':
            raise RuntimeError(f'cannot convert unsupported file type: {source_path.name}')

        wav_path = source_path.with_suffix('.wav')
        needs_convert = (
            not wav_path.exists()
            or source_path.stat().st_mtime > wav_path.stat().st_mtime
        )
        if not needs_convert:
            return wav_path

        completed = subprocess.run(
            ['ffmpeg', '-y', '-i', str(source_path), str(wav_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or 'unknown error'
            raise RuntimeError(f'ffmpeg convert failed: {stderr}')
        return wav_path

    def _play_wav_file(self, wav_path: Path):
        completed = subprocess.run(
            ['aplay', '-D', self.audio_device, str(wav_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or 'unknown error'
            raise RuntimeError(f'aplay failed: {stderr}')

    def _run_shell_command(self, command: str, action_name: str):
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or 'unknown error'
            raise RuntimeError(f'{action_name} command failed: {stderr}')


class TcpControlClient:
    STOP_BUTTON = '$011504001A#'
    BRAKE_BUTTON = '$0115040721#'

    def __init__(self, host: str, port: int, speed: int = 80, timeout_sec: float = 1.5):
        self.host = host.strip()
        self.port = int(port)
        self.speed = max(0, min(100, int(speed)))
        self.timeout_sec = float(timeout_sec)

    def is_enabled(self) -> bool:
        return bool(self.host and self.port > 0)

    def send_command(self, cmd: str):
        frame = self._command_to_frame(cmd)
        if frame is None:
            raise RuntimeError(f'unsupported tcp command: {cmd}')
        self._send_frame(frame)

    def _command_to_frame(self, cmd: str):
        if cmd == 'forward':
            return self._rocker_frame('front', self.speed)
        if cmd == 'backward':
            return self._rocker_frame('back', self.speed)
        if cmd == 'left':
            return self._rocker_frame('left_rotate', self.speed)
        if cmd == 'right':
            return self._rocker_frame('right_rotate', self.speed)
        if cmd == 'emergency_stop':
            return self.BRAKE_BUTTON
        if cmd in {'stop', 'reset_emergency'}:
            return self.STOP_BUTTON
        return None

    def _send_frame(self, frame: str):
        with socket.create_connection((self.host, self.port), timeout=self.timeout_sec) as sock:
            sock.sendall(frame.encode('ascii'))

    def _rocker_frame(self, action: str, speed: int):
        x, y = self._action_xy(action, speed)
        body = f'{self._to_byte(x):02X}{self._to_byte(y):02X}'
        payload = '01' + '10' + '06' + body
        return '$' + payload + self._checksum(payload) + '#'

    def _action_xy(self, action: str, speed: int):
        table = {
            'front': (0, speed),
            'back': (0, -speed),
            'left': (-speed, 0),
            'right': (speed, 0),
            'left_rotate': (-speed, speed),
            'right_rotate': (speed, -speed),
            'stop': (0, 0),
        }
        return table[action]

    def _checksum(self, hex_data: str):
        total = 0
        for index in range(0, len(hex_data), 2):
            total = (total + int(hex_data[index:index + 2], 16)) % 256
        return f'{total:02X}'

    def _to_byte(self, value: int):
        value = max(-100, min(100, int(value)))
        return value + 256 if value < 0 else value


class AppControlGateway(Node):
    def __init__(self):
        super().__init__('app_control_gateway')
        self.publisher = self.create_publisher(Twist, '/cmd_vel', 10)
        self.fall_alert_pub = self.create_publisher(Bool, '/ai/fall_alert', 10)
        self.risk_level_pub = self.create_publisher(String, '/ai/risk_level', 10)
        self.fall_alert_sub = self.create_subscription(
            Bool, '/ai/fall_alert', self.on_fall_alert, 10
        )
        self.risk_level_sub = self.create_subscription(
            String, '/ai/risk_level', self.on_risk_level, 10
        )
        self.obstacle_status_sub = self.create_subscription(
            String, '/obstacle_status', self.on_obstacle_status, 10
        )
        self.navigation_status_sub = self.create_subscription(
            String, '/navigation_status', self.on_navigation_status, 10
        )
        self.inspection_event_sub = self.create_subscription(
            String, '/robot/inspection_event', self.on_inspection_event, 10
        )

        self.state_lock = threading.Lock()
        self.current_twist = Twist()
        self.current_cmd = 'stop'
        self.fall_alert = False
        self.risk_level = 'low'
        self.obstacle_status = 'unknown'
        self.navigation_status = 'unknown'
        self.control_connected = True
        self.emergency_stop = False
        self.control_block_reason = 'none'
        self.last_command_time = time.time()
        self.timeout_sec = 2.0

        self.allowed_risk_levels = {'low', 'medium', 'high'}
        self.allowed_obstacle_statuses = {'safe', 'obstacle', 'unknown'}
        self.allowed_navigation_statuses = {
            'idle', 'running', 'arrived', 'failed', 'paused', 'unknown'
        }
        self.required_inspection_fields = {
            'type', 'title', 'x', 'y', 'level', 'status', 'source', 'time'
        }
        self.use_tcp_control = os.getenv('APP_CONTROL_USE_TCP_CONTROL', 'false').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
        self.publish_cmd_vel = os.getenv('APP_CONTROL_PUBLISH_CMD_VEL', 'true').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
        self.tcp_control_client = TcpControlClient(
            host=os.getenv('APP_CONTROL_TCP_HOST', ''),
            port=int(os.getenv('APP_CONTROL_TCP_PORT', '6000')),
            speed=int(os.getenv('APP_CONTROL_TCP_SPEED', '80')),
            timeout_sec=float(os.getenv('APP_CONTROL_TCP_TIMEOUT_SEC', '1.5')),
        )
        self.inspection_forward_client = InspectionForwardClient(
            base_url=os.getenv('SPRINGBOOT_BASE_URL', '').strip(),
            timeout_sec=float(os.getenv('SPRINGBOOT_HTTP_TIMEOUT_SEC', '5.0')),
        )
        self.last_inspection_event = {}
        self.last_inspection_forward_status = 'idle'
        self.last_inspection_forward_error = ''

        self.entertainment_backend = EntertainmentClient(
            base_url=os.getenv('ENTERTAINMENT_BACKEND_BASE_URL', '').strip(),
            timeout_sec=float(os.getenv('ENTERTAINMENT_HTTP_TIMEOUT_SEC', '5.0')),
        )
        self.entertainment_executor = EntertainmentExecutor(
            music_dir=os.getenv('ENTERTAINMENT_MUSIC_DIR', '/root/Control_demo/music'),
            player_command=os.getenv('ENTERTAINMENT_PLAYER_CMD', ''),
            dance_command=os.getenv('ENTERTAINMENT_DANCE_CMD', ''),
        )
        self.entertainment_poll_interval_sec = float(
            os.getenv('ENTERTAINMENT_POLL_INTERVAL_SEC', '2.0')
        )
        self.sos_alarm_poll_interval_sec = float(
            os.getenv('SOS_ALARM_POLL_INTERVAL_SEC', '2.0')
        )
        self.entertainment_enabled = self.entertainment_backend.is_enabled()
        self.entertainment_running = False
        self.entertainment_last_error = ''
        self.entertainment_last_task = {}
        self.entertainment_seen_task_ids = set()

        self.timer = self.create_timer(0.1, self.publish_current_twist)
        self.get_logger().info('app_control_gateway started')

    def on_fall_alert(self, msg: Bool):
        new_value = bool(msg.data)
        with self.state_lock:
            if self.fall_alert != new_value:
                self.get_logger().info(
                    f'fall_alert changed: {self.fall_alert} -> {new_value}'
                )
            self.fall_alert = new_value

    def on_risk_level(self, msg: String):
        risk_level = msg.data.strip().lower()
        if risk_level not in self.allowed_risk_levels:
            self.get_logger().warning(
                f'ignored invalid risk_level: {msg.data!r}'
            )
            return

        with self.state_lock:
            if self.risk_level != risk_level:
                self.get_logger().info(
                    f'risk_level changed: {self.risk_level} -> {risk_level}'
                )
            self.risk_level = risk_level

    def on_obstacle_status(self, msg: String):
        obstacle_status = msg.data.strip().lower()
        if obstacle_status not in self.allowed_obstacle_statuses:
            self.get_logger().warning(
                f'ignored invalid obstacle_status: {msg.data!r}'
            )
            return

        with self.state_lock:
            if self.obstacle_status != obstacle_status:
                self.get_logger().info(
                    f'obstacle_status changed: {self.obstacle_status} -> {obstacle_status}'
                )
            self.obstacle_status = obstacle_status

    def on_navigation_status(self, msg: String):
        navigation_status = msg.data.strip().lower()
        if navigation_status not in self.allowed_navigation_statuses:
            self.get_logger().warning(
                f'ignored invalid navigation_status: {msg.data!r}'
            )
            return

        with self.state_lock:
            if self.navigation_status != navigation_status:
                self.get_logger().info(
                    f'navigation_status changed: {self.navigation_status} -> {navigation_status}'
                )
            self.navigation_status = navigation_status

    def on_inspection_event(self, msg: String):
        raw_payload = msg.data.strip()
        self.get_logger().info(f'received /robot/inspection_event: {raw_payload}')

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            with self.state_lock:
                self.last_inspection_forward_status = 'invalid_json'
                self.last_inspection_forward_error = str(exc)
            self.get_logger().warning(f'inspection_event invalid json: {exc}')
            return

        if not isinstance(payload, dict):
            with self.state_lock:
                self.last_inspection_forward_status = 'invalid_payload'
                self.last_inspection_forward_error = 'payload is not a json object'
            self.get_logger().warning(
                'inspection_event ignored: payload is not a json object'
            )
            return

        missing_fields = [
            field for field in sorted(self.required_inspection_fields)
            if field not in payload
        ]
        if missing_fields:
            with self.state_lock:
                self.last_inspection_event = payload
                self.last_inspection_forward_status = 'missing_fields'
                self.last_inspection_forward_error = ','.join(missing_fields)
            self.get_logger().warning(
                f'inspection_event ignored: missing fields {missing_fields}'
            )
            return

        with self.state_lock:
            self.last_inspection_event = payload

        if not self.inspection_forward_client.is_enabled():
            with self.state_lock:
                self.last_inspection_forward_status = 'forward_disabled'
                self.last_inspection_forward_error = 'SPRINGBOOT_BASE_URL is not configured'
            self.get_logger().warning(
                'inspection_event forwarding skipped: SPRINGBOOT_BASE_URL is not configured'
            )
            return

        try:
            self.inspection_forward_client.forward_marker(payload)
            with self.state_lock:
                self.last_inspection_forward_status = 'forwarded'
                self.last_inspection_forward_error = ''
            self.get_logger().info(
                'inspection_event forwarded success: '
                f'type={payload.get("type")} title={payload.get("title")}'
            )
        except urllib.error.HTTPError as exc:
            with self.state_lock:
                self.last_inspection_forward_status = f'http_{exc.code}'
                self.last_inspection_forward_error = str(exc)
            self.get_logger().warning(
                f'inspection_event forwarded failed: http {exc.code}'
            )
        except Exception as exc:
            with self.state_lock:
                self.last_inspection_forward_status = 'forward_failed'
                self.last_inspection_forward_error = str(exc)
            self.get_logger().warning(
                f'inspection_event forwarded failed: {exc}'
            )

    def set_ai_status(self, fall_alert, risk_level):
        if not isinstance(fall_alert, bool):
            return False, 'fall_alert must be a boolean'

        normalized_risk_level = str(risk_level).strip().lower()
        if normalized_risk_level not in self.allowed_risk_levels:
            return False, f'invalid risk_level: {normalized_risk_level}'

        with self.state_lock:
            self.fall_alert = fall_alert
            self.risk_level = normalized_risk_level

        alert_msg = Bool()
        alert_msg.data = fall_alert
        risk_msg = String()
        risk_msg.data = normalized_risk_level
        self.fall_alert_pub.publish(alert_msg)
        self.risk_level_pub.publish(risk_msg)
        self.get_logger().info(
            'AI status updated from HTTP: '
            f'fall_alert={fall_alert}, risk_level={normalized_risk_level}'
        )
        return True, None

    def command_to_twist(self, cmd: str):
        msg = Twist()

        if cmd == 'forward':
            msg.linear.x = 0.2
        elif cmd == 'backward':
            msg.linear.x = -0.2
        elif cmd == 'left':
            msg.angular.z = 0.8
        elif cmd == 'right':
            msg.angular.z = -0.8
        elif cmd in ('stop', 'emergency_stop'):
            pass
        else:
            return None

        return msg

    def set_command(self, cmd: str):
        if cmd == 'reset_emergency':
            with self.state_lock:
                previous_cmd = self.current_cmd
                self.emergency_stop = False
                self.current_cmd = 'stop'
                self.current_twist = Twist()
                self.last_command_time = time.time()
                self.control_connected = True
                self.control_block_reason = 'none'

                self.get_logger().info(
                    f'received command: reset_emergency (previous={previous_cmd})'
                )

            if self.use_tcp_control and self.tcp_control_client.is_enabled():
                self.tcp_control_client.send_command('reset_emergency')

            return True, None

        msg = self.command_to_twist(cmd)
        if msg is None:
            return False, f'unsupported command: {cmd}'

        with self.state_lock:
            if self.emergency_stop and cmd not in ('stop', 'emergency_stop'):
                self.get_logger().warning(
                    f'blocked {cmd} because emergency_stop is active'
                )
                self.control_block_reason = 'emergency_stop'
                return False, 'blocked by emergency_stop'

            if cmd == 'forward' and self.obstacle_status == 'obstacle':
                self.get_logger().warning(
                    'blocked forward because obstacle_status is obstacle'
                )
                self.control_block_reason = 'obstacle'
                return False, 'blocked by obstacle'

            previous_cmd = self.current_cmd
            self.current_cmd = cmd
            self.current_twist = msg
            self.last_command_time = time.time()
            self.control_connected = True
            self.control_block_reason = 'none'

            if cmd == 'emergency_stop':
                self.emergency_stop = True
                self.control_block_reason = 'emergency_stop'
            elif cmd == 'stop':
                self.emergency_stop = False

            self.get_logger().info(
                f'received command: {cmd} (previous={previous_cmd}, emergency_stop={self.emergency_stop})'
            )

        if self.use_tcp_control and self.tcp_control_client.is_enabled():
            try:
                self.tcp_control_client.send_command(cmd)
            except Exception as exc:
                self.get_logger().warning(
                    f'tcp control send failed for {cmd}: {exc}'
                )
                return False, f'tcp control send failed: {exc}'

        return True, None

    def publish_current_twist(self):
        now = time.time()
        should_send_tcp_stop = False
        tcp_stop_reason = ''
        with self.state_lock:
            elapsed = now - self.last_command_time
            timed_out = elapsed > self.timeout_sec

            if timed_out and self.current_cmd not in ('stop', 'emergency_stop'):
                self.get_logger().warning(
                    f'timeout stop triggered after {elapsed:.2f}s'
                )
                self.current_cmd = 'stop'
                self.current_twist = Twist()
                self.control_connected = False
                self.control_block_reason = 'timeout'
                should_send_tcp_stop = True
                tcp_stop_reason = 'timeout'

            if self.emergency_stop:
                self.current_cmd = 'emergency_stop'
                self.current_twist = Twist()
                self.control_block_reason = 'emergency_stop'
                should_send_tcp_stop = True
                tcp_stop_reason = 'emergency_stop'

            if self.obstacle_status == 'obstacle' and self.current_cmd == 'forward':
                self.get_logger().warning(
                    'forward motion stopped because obstacle_status became obstacle'
                )
                self.current_cmd = 'stop'
                self.current_twist = Twist()
                self.control_block_reason = 'obstacle'
                should_send_tcp_stop = True
                tcp_stop_reason = 'obstacle'

            current_twist = self.current_twist

        if self.publish_cmd_vel:
            self.publisher.publish(current_twist)

        if should_send_tcp_stop and self.use_tcp_control and self.tcp_control_client.is_enabled():
            try:
                stop_cmd = 'emergency_stop' if tcp_stop_reason == 'emergency_stop' else 'stop'
                self.tcp_control_client.send_command(stop_cmd)
            except Exception as exc:
                self.get_logger().warning(
                    f'tcp control stop send failed ({tcp_stop_reason}): {exc}'
                )

    def entertainment_worker_loop(self):
        if not self.entertainment_enabled:
            self.get_logger().info('entertainment polling disabled')
            return

        self.get_logger().info(
            f'entertainment polling enabled: {self.entertainment_backend.base_url}'
        )

        while rclpy.ok():
            try:
                tasks = self.entertainment_backend.fetch_pending_tasks()
                if tasks:
                    for task in tasks:
                        task_id = task.get('taskId')
                        if task_id in self.entertainment_seen_task_ids:
                            continue
                        self._handle_entertainment_task(task)
                else:
                    with self.state_lock:
                        self.entertainment_running = False
                time.sleep(self.entertainment_poll_interval_sec)
            except Exception as exc:
                self.get_logger().warning(f'entertainment polling failed: {exc}')
                with self.state_lock:
                    self.entertainment_running = False
                    self.entertainment_last_error = str(exc)
                time.sleep(self.entertainment_poll_interval_sec)

    def _handle_entertainment_task(self, task: dict):
        task_id = task.get('taskId')
        if task_id is None:
            raise RuntimeError('taskId is missing in pending task')

        task_type = str(task.get('taskType') or '').strip()
        music_name = str(task.get('musicName') or '').strip()
        self.get_logger().info(
            f'entertainment task received: taskId={task_id}, taskType={task_type}, musicName={music_name}'
        )

        with self.state_lock:
            self.entertainment_running = True
            self.entertainment_last_error = ''
            self.entertainment_last_task = {
                'taskId': task_id,
                'taskType': task_type,
                'musicName': music_name,
                'danceMode': str(task.get('danceMode') or ''),
            }

        request_meta = task.get('requestJson')
        if request_meta is None:
            request_meta = task.get('request_json')
        if not isinstance(request_meta, dict):
            request_meta = {}

        is_sos_alarm = (
            task_type.lower() == 'music'
            and str(request_meta.get('purpose') or '').strip().lower() == 'sos_alarm'
        )

        if is_sos_alarm:
            try:
                alert_id = request_meta.get('alertId')
                if alert_id is None:
                    raise RuntimeError('alertId is missing for sos_alarm task')

                self.entertainment_backend.update_task_status(
                    task_id,
                    'running',
                    'SOS alarm audio playing',
                )
                self.entertainment_executor.execute_sos_alarm(
                    task,
                    self.entertainment_backend,
                    alert_id,
                    poll_interval_sec=self.sos_alarm_poll_interval_sec,
                )
                self.entertainment_backend.update_task_status(
                    task_id,
                    'completed',
                    'SOS alarm audio stopped',
                )
                self.entertainment_seen_task_ids.add(task_id)
                with self.state_lock:
                    self.entertainment_running = False
                return
            except Exception as exc:
                message = str(exc)
                self.entertainment_backend.update_task_status(
                    task_id,
                    'failed',
                    message,
                )
                self.entertainment_seen_task_ids.add(task_id)
                with self.state_lock:
                    self.entertainment_running = False
                    self.entertainment_last_error = message
                self.get_logger().warning(
                    f'SOS entertainment task failed: taskId={task_id}, reason={message}'
                )
                return

        self.entertainment_backend.update_task_status(
            task_id,
            'running',
            '小车开始执行任务',
        )

        try:
            self.entertainment_executor.execute_task(task)
            self.entertainment_backend.update_task_status(
                task_id,
                'completed',
                '任务执行完成',
            )
            self.entertainment_seen_task_ids.add(task_id)
            with self.state_lock:
                self.entertainment_running = False
        except Exception as exc:
            message = str(exc)
            self.entertainment_backend.update_task_status(
                task_id,
                'failed',
                message,
            )
            self.entertainment_seen_task_ids.add(task_id)
            with self.state_lock:
                self.entertainment_running = False
                self.entertainment_last_error = message
            self.get_logger().warning(
                f'entertainment task failed: taskId={task_id}, reason={message}'
            )

    def get_state(self):
        with self.state_lock:
            return {
                'current_cmd': self.current_cmd,
                'fall_alert': self.fall_alert,
                'risk_level': self.risk_level,
                'obstacle_status': self.obstacle_status,
                'navigation_status': self.navigation_status,
                'control_connected': self.control_connected,
                'emergency_stop': self.emergency_stop,
                'control_block_reason': self.control_block_reason,
                'last_command_time': self.last_command_time,
                'timeout_sec': self.timeout_sec,
                'control_transport': 'tcp' if self.use_tcp_control else 'ros2_cmd_vel',
                'tcp_control_enabled': self.use_tcp_control and self.tcp_control_client.is_enabled(),
                'last_inspection_event': self.last_inspection_event,
                'last_inspection_forward_status': self.last_inspection_forward_status,
                'last_inspection_forward_error': self.last_inspection_forward_error,
                'entertainment_enabled': self.entertainment_enabled,
                'entertainment_running': self.entertainment_running,
                'entertainment_last_error': self.entertainment_last_error,
                'entertainment_last_task': self.entertainment_last_task,
            }


def register_routes(app: Flask, node: AppControlGateway):
    def handle_command_request(require_structured_type: bool):
        data = request.get_json(silent=True) or {}
        raw_cmd = data.get('cmd', '')

        if require_structured_type and data.get('type', '') != 'control':
            return jsonify({
                'ok': False,
                'error': 'type must be "control"',
            }), 400

        if not isinstance(raw_cmd, str):
            return jsonify({
                'ok': False,
                'error': 'cmd must be a string',
            }), 400

        cmd = raw_cmd.strip()
        accepted, error = node.set_command(cmd)

        if not accepted:
            status_code = 400 if error and 'unsupported' in error else 409
            return jsonify({
                'ok': False,
                'error': error,
            }), status_code

        return jsonify({
            'ok': True,
            'type': 'control',
            'cmd': cmd,
        })

    @app.after_request
    def add_cors_headers(response):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        return response

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({
            'ok': True,
            **node.get_state(),
        })

    @app.route('/', methods=['GET'])
    def index():
        return jsonify({
            'ok': True,
            'service': 'app_control_gateway',
            'endpoints': {
                'health': '/health',
                'state': '/api/state',
                'command': '/api/command',
                'ai_status': '/api/ai/status',
                'inspection_event': '/api/inspection/event',
            },
        })

    @app.route('/api/state', methods=['GET'])
    def api_state():
        return jsonify(node.get_state())

    @app.route('/api/ai/status', methods=['POST'])
    def api_ai_status():
        data = request.get_json(silent=True) or {}
        accepted, error = node.set_ai_status(
            data.get('fall_alert'),
            data.get('risk_level', 'unknown'),
        )
        if not accepted:
            return jsonify({
                'ok': False,
                'error': error,
            }), 400

        return jsonify({
            'ok': True,
            'fall_alert': data['fall_alert'],
            'risk_level': str(data['risk_level']).strip().lower(),
        })

    @app.route('/api/inspection/event', methods=['POST'])
    def api_inspection_event():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({
                'ok': False,
                'error': 'request body must be a JSON object',
            }), 400

        message = String()
        message.data = json.dumps(payload, ensure_ascii=False)
        node.on_inspection_event(message)

        with node.state_lock:
            forward_status = node.last_inspection_forward_status
            forward_error = node.last_inspection_forward_error

        if forward_status != 'forwarded':
            return jsonify({
                'ok': False,
                'forwardStatus': forward_status,
                'error': forward_error,
            }), 502

        return jsonify({
            'ok': True,
            'forwardStatus': forward_status,
            'eventType': payload.get('type'),
        })

    @app.route('/api/command', methods=['POST'])
    def api_command():
        return handle_command_request(require_structured_type=True)

    @app.route('/command', methods=['POST'])
    def legacy_command():
        return handle_command_request(require_structured_type=False)

    return app


def create_app(node: AppControlGateway):
    app = Flask(__name__)
    return register_routes(app, node)


def ros_spin(node: AppControlGateway):
    rclpy.spin(node)


def start_gateway_node():
    rclpy.init()
    node = AppControlGateway()

    ros_thread = threading.Thread(target=ros_spin, args=(node,), daemon=True)
    ros_thread.start()

    entertainment_thread = threading.Thread(
        target=node.entertainment_worker_loop,
        daemon=True,
    )
    entertainment_thread.start()

    return node, ros_thread, entertainment_thread


def stop_gateway_node(node: AppControlGateway):
    node.set_command('stop')
    node.destroy_node()
    rclpy.shutdown()


def main():
    node, _, _ = start_gateway_node()
    app = create_app(node)
    http_host = os.getenv('APP_CONTROL_HTTP_HOST', '0.0.0.0').strip() or '0.0.0.0'
    http_port = int(os.getenv('APP_CONTROL_HTTP_PORT', '5000'))

    try:
        app.run(host=http_host, port=http_port, debug=False)
    finally:
        stop_gateway_node(node)


if __name__ == '__main__':
    main()

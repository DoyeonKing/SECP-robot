from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional


FaceDetector = Callable[[Any], Dict[str, Any]]
FallDetector = Callable[[Any], Dict[str, Any]]
CrackDetector = Callable[[Any], Optional[Dict[str, Any]]]
CrackContextUpdater = Callable[..., None]
CrackEvidenceGetter = Callable[[], Any]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{now_text()}] {message}", flush=True)


def save_evidence_image(frame: Any, event_type: str, config: "RunnerConfig") -> dict[str, str]:
    if frame is None:
        return {}

    import cv2

    output_dir = Path(config.evidence_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{event_type}_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
    output_path = output_dir / filename
    if not cv2.imwrite(str(output_path), frame):
        log(f"failed to save {event_type} evidence: {output_path}")
        return {}

    result = {"imagePath": str(output_path)}
    if config.evidence_base_url.strip():
        result["imageUrl"] = urllib.parse.urljoin(
            config.evidence_base_url.rstrip("/") + "/",
            urllib.parse.quote(filename),
        )
    log(f"saved {event_type} evidence: {output_path}")
    return result


@dataclass
class RunnerConfig:
    camera_index: int
    springboot_base_url: str
    report_mode: str
    current_x: float
    current_y: float
    current_location_name: str
    face_interval: int
    crack_cooldown: float
    crack_enabled: bool
    fall_cooldown: float
    evidence_dir: str
    evidence_base_url: str
    gateway_base_url: str
    mock: bool
    max_frames: int | None


class HttpReporter:
    def __init__(self, springboot_base_url: str) -> None:
        self.endpoint = springboot_base_url.rstrip("/") + "/api/inspection/markers"

    def report(self, event: dict[str, Any]) -> bool:
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
                ok = 200 <= response.status < 300
                log(f"http report status={response.status} body={body[:200]}")
                return ok
        except urllib.error.URLError as exc:
            log(f"http report failed: {exc}")
            return False


class GatewayStatusReporter:
    def __init__(self, gateway_base_url: str) -> None:
        self.endpoint = gateway_base_url.rstrip("/") + "/api/ai/status"
        self._last_fall_alert: bool | None = None
        self._last_risk_level: str | None = None
        self._last_report_time = 0.0

    def publish_fall_status(self, fall_alert: bool, risk_level: str) -> None:
        now = time.time()
        changed = (
            fall_alert != self._last_fall_alert
            or risk_level != self._last_risk_level
        )
        if not changed and now - self._last_report_time < 1.0:
            return

        payload = json.dumps({
            "fall_alert": fall_alert,
            "risk_level": risk_level,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read()
                if not 200 <= response.status < 300:
                    log(f"gateway status report failed: HTTP {response.status}")
                    return
            self._last_fall_alert = fall_alert
            self._last_risk_level = risk_level
            self._last_report_time = now
            log(
                "gateway fall status "
                f"fall_alert={fall_alert} risk_level={risk_level}"
            )
        except urllib.error.URLError as exc:
            log(f"gateway status report failed: {exc}")


class GatewayEventReporter:
    def __init__(self, gateway_base_url: str) -> None:
        self.endpoint = gateway_base_url.rstrip("/") + "/api/inspection/event"

    def report(self, event: dict[str, Any]) -> bool:
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
                ok = 200 <= response.status < 300
                log(f"gateway event status={response.status} body={body[:200]}")
                return ok
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            log(f"gateway event failed: HTTP {exc.code} body={body[:200]}")
            return False
        except urllib.error.URLError as exc:
            log(f"gateway event failed: {exc}")
            return False


class RosTopicReporter:
    def __init__(self) -> None:
        try:
            import rclpy
            from rclpy.node import Node
            from std_msgs.msg import Bool, String
        except Exception as exc:  # pragma: no cover - depends on ROS2 env
            raise RuntimeError(
                "ROS2 modules are not available. Use --report-mode http or run inside ROS2 environment."
            ) from exc

        self._rclpy = rclpy
        self._bool_cls = Bool
        self._string_cls = String
        self._last_fall_alert: bool | None = None
        self._last_risk_level: str | None = None
        self._last_status_publish_time = 0.0
        rclpy.init(args=None)

        class _InspectionEventNode(Node):
            def __init__(self, bool_cls: Any, string_cls: Any) -> None:
                super().__init__("patrol_ai_runner")
                self.publisher = self.create_publisher(
                    string_cls,
                    "/robot/inspection_event",
                    10,
                )
                self.fall_alert_publisher = self.create_publisher(
                    bool_cls,
                    "/ai/fall_alert",
                    10,
                )
                self.risk_level_publisher = self.create_publisher(
                    string_cls,
                    "/ai/risk_level",
                    10,
                )

        self._node = _InspectionEventNode(Bool, String)

    def report(self, event: dict[str, Any]) -> bool:
        message = self._string_cls()
        message.data = json.dumps(event, ensure_ascii=False)
        self._node.publisher.publish(message)
        self._rclpy.spin_once(self._node, timeout_sec=0.0)
        log(f"ros topic report /robot/inspection_event {message.data}")
        return True

    def publish_fall_status(self, fall_alert: bool, risk_level: str) -> None:
        now = time.time()
        changed = (
            fall_alert != self._last_fall_alert
            or risk_level != self._last_risk_level
        )
        if not changed and now - self._last_status_publish_time < 1.0:
            return

        alert_message = self._bool_cls()
        alert_message.data = fall_alert
        risk_message = self._string_cls()
        risk_message.data = risk_level
        self._node.fall_alert_publisher.publish(alert_message)
        self._node.risk_level_publisher.publish(risk_message)
        self._rclpy.spin_once(self._node, timeout_sec=0.0)
        self._last_fall_alert = fall_alert
        self._last_risk_level = risk_level
        self._last_status_publish_time = now
        log(
            "ros fall status "
            f"/ai/fall_alert={fall_alert} /ai/risk_level={risk_level}"
        )

    def close(self) -> None:
        self._node.destroy_node()
        self._rclpy.shutdown()


def load_modules(mock: bool) -> tuple[
    FaceDetector,
    FallDetector,
    CrackDetector,
    CrackContextUpdater,
    CrackEvidenceGetter,
]:
    if mock:
        log("using mock detector modules")
        return (
            mock_recognize_face,
            mock_detect_fall,
            mock_detect_crack,
            mock_update_crack_marker_context,
            mock_get_last_crack_evidence,
        )

    try:
        from face_recognizer import recognize_face
        from fall_detector import detect_fall
        from crack_detect_camera import (
            detect_crack,
            get_last_crack_evidence,
            update_crack_marker_context,
        )
    except Exception as exc:
        raise RuntimeError(
            "Failed to import detector modules. Put face_recognizer.py, "
            "fall_detector.py, and crack_detect_camera.py beside this script, "
            "or run with --mock."
        ) from exc

    return (
        recognize_face,
        detect_fall,
        detect_crack,
        update_crack_marker_context,
        get_last_crack_evidence,
    )


def mock_recognize_face(frame: Any) -> dict[str, Any]:
    return {
        "status": "matched",
        "elderProfileId": os.getenv("PATROL_ELDER_PROFILE_ID", ""),
        "elderCode": os.getenv("PATROL_ELDER_CODE", ""),
        "similarity": 0.81,
        "bbox": [120, 80, 280, 260],
        "face_count": 1,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def mock_detect_fall(frame: Any) -> dict[str, Any]:
    second = int(time.time())
    if second % 30 == 0:
        return {"fall_alert": True, "risk_level": "high"}
    return {"fall_alert": False, "risk_level": "low"}


def mock_update_crack_marker_context(**kwargs: Any) -> None:
    return None


def mock_get_last_crack_evidence() -> Any:
    return None


def mock_detect_crack(frame: Any) -> dict[str, Any] | None:
    second = int(time.time())
    if second % 45 == 0:
        return {
            "type": "crack",
            "title": "检测到地面裂缝",
            "description": "mock: 小车检测到疑似地面裂缝",
            "x": 320,
            "y": 190,
            "level": "warning",
            "status": "unhandled",
            "source": "vision",
            "time": now_text(),
            "locationName": "一层东侧走廊",
            "confidence": 0.91,
        }
    return None


def is_face_matched(face_result: dict[str, Any] | None) -> bool:
    return bool(face_result and face_result.get("status") == "matched")


def identity_from_face(
    current_face: dict[str, Any] | None,
    recent_identity: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    if is_face_matched(current_face):
        return current_face, "current_frame"
    if is_face_matched(recent_identity):
        return recent_identity, "recent_identity"
    return None, "unknown"


def build_fall_event(
    fall_result: dict[str, Any],
    current_face: dict[str, Any] | None,
    recent_identity: dict[str, Any] | None,
    config: RunnerConfig,
) -> dict[str, Any]:
    identity, identity_source = identity_from_face(current_face, recent_identity)
    risk_level = str(fall_result.get("risk_level", "unknown"))

    elder_profile_id = ""
    elder_name = "未知人员"
    identity_confidence = 0.0
    notified_child = False

    if identity is not None:
        elder_profile_id = str(identity.get("elderProfileId") or "")
        elder_name = str(identity.get("elderCode") or elder_profile_id or "未知人员")
        identity_confidence = float(identity.get("similarity") or 0.0)
        notified_child = bool(elder_profile_id)

    return {
        "type": "fall",
        "title": f"{elder_name}疑似跌倒",
        "description": f"跌倒检测触发，风险等级 {risk_level}",
        "x": config.current_x,
        "y": config.current_y,
        "level": "danger",
        "status": "unhandled",
        "source": "yolo",
        "time": now_text(),
        "locationName": config.current_location_name,
        "elderProfileId": elder_profile_id,
        "elderName": elder_name,
        "identitySource": identity_source,
        "identityConfidence": identity_confidence,
        "notifiedChild": notified_child,
    }


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def create_reporter(
    report_mode: str,
    springboot_base_url: str,
    gateway_base_url: str,
) -> Any:
    if report_mode == "http":
        return HttpReporter(springboot_base_url)
    if report_mode == "gateway_http":
        return GatewayEventReporter(gateway_base_url)
    if report_mode == "ros_topic":
        return RosTopicReporter()
    raise ValueError(f"unsupported report mode: {report_mode}")


def run(config: RunnerConfig) -> int:
    try:
        import cv2
    except Exception as exc:
        log("failed to import cv2. Install opencv-python or run on the Jetson environment.")
        log(f"cv2 import error: {exc}")
        return 2

    (
        recognize_face,
        detect_fall,
        detect_crack,
        update_crack_marker_context,
        get_last_crack_evidence,
    ) = load_modules(config.mock)
    reporter = create_reporter(
        config.report_mode,
        config.springboot_base_url,
        config.gateway_base_url,
    )
    fall_status_publisher = (
        reporter
        if isinstance(reporter, RosTopicReporter)
        else GatewayStatusReporter(config.gateway_base_url)
    )

    cap = cv2.VideoCapture(config.camera_index)
    if not cap.isOpened():
        log(f"failed to open camera index {config.camera_index}")
        return 2

    recent_identity: dict[str, Any] | None = None
    last_face_result: dict[str, Any] | None = None
    last_crack_report_time = 0.0
    last_fall_report_time = 0.0
    frame_count = 0

    log(
        "patrol runner started "
        f"camera_index={config.camera_index} report_mode={config.report_mode} "
        f"mock={config.mock}"
    )

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                log("frame read failed")
                time.sleep(0.05)
                continue

            frame_count += 1
            if config.max_frames is not None and frame_count > config.max_frames:
                log(f"max_frames reached: {config.max_frames}")
                break

            log(f"frame ok #{frame_count}")
            fall_result = detect_fall(frame)
            log(f"fall result: {json.dumps(fall_result, ensure_ascii=False)}")

            fall_alert = normalize_bool(fall_result.get("fall_alert"))
            risk_level = str(fall_result.get("risk_level", "unknown")).strip().lower()
            fall_status_publisher.publish_fall_status(fall_alert, risk_level)
            should_run_face = frame_count % config.face_interval == 0 or fall_alert
            current_face_result: dict[str, Any] | None = None
            if should_run_face:
                current_face_result = recognize_face(frame)
                last_face_result = current_face_result
                log(f"face result: {json.dumps(current_face_result, ensure_ascii=False)}")
                if is_face_matched(current_face_result):
                    recent_identity = current_face_result
                    log(
                        "recent identity updated: "
                        f"{recent_identity.get('elderProfileId')} "
                        f"{recent_identity.get('elderCode')}"
                    )

            crack_result = None
            if config.crack_enabled:
                update_crack_marker_context(
                    x=config.current_x,
                    y=config.current_y,
                    location_name=config.current_location_name,
                )
                crack_result = detect_crack(frame)
                log(f"crack result: {json.dumps(crack_result, ensure_ascii=False)}")

            now = time.time()
            if fall_alert and now - last_fall_report_time >= config.fall_cooldown:
                face_for_event = current_face_result or last_face_result
                fall_event = build_fall_event(
                    fall_result,
                    face_for_event,
                    recent_identity,
                    config,
                )
                fall_event.update(save_evidence_image(frame.copy(), "fall", config))
                if reporter.report(fall_event):
                    last_fall_report_time = now
                    log(f"event uploaded: {json.dumps(fall_event, ensure_ascii=False)}")

            if (
                crack_result is not None
                and now - last_crack_report_time >= config.crack_cooldown
            ):
                crack_result.update(
                    save_evidence_image(
                        get_last_crack_evidence(),
                        "crack",
                        config,
                    )
                )
                if reporter.report(crack_result):
                    last_crack_report_time = now
                    log(f"event uploaded: {json.dumps(crack_result, ensure_ascii=False)}")

            time.sleep(0.02)
    except KeyboardInterrupt:
        log("stopped by user")
    finally:
        cap.release()
        if fall_status_publisher is not reporter:
            status_close = getattr(fall_status_publisher, "close", None)
            if callable(status_close):
                status_close()
        close = getattr(reporter, "close", None)
        if callable(close):
            close()

    return 0


def parse_args(argv: list[str]) -> RunnerConfig:
    parser = argparse.ArgumentParser(description="Smart car patrol AI runner")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--springboot-base-url", default="http://localhost:8080")
    parser.add_argument(
        "--report-mode",
        choices=["http", "gateway_http", "ros_topic"],
        default="http",
    )
    parser.add_argument("--current-x", type=float, default=320)
    parser.add_argument("--current-y", type=float, default=190)
    parser.add_argument("--current-location-name", default="一层东侧走廊")
    parser.add_argument("--face-interval", type=int, default=10)
    parser.add_argument("--crack-cooldown", type=float, default=10.0)
    parser.add_argument(
        "--disable-crack",
        action="store_true",
        help="Disable crack detection, evidence capture, logging, and reporting.",
    )
    parser.add_argument("--fall-cooldown", type=float, default=10.0)
    parser.add_argument(
        "--evidence-dir",
        default="/home/jetson/patrol_ai/evidence",
        help="Local directory used to save fall and crack evidence images.",
    )
    parser.add_argument(
        "--evidence-base-url",
        default="",
        help="Optional public URL prefix serving files from --evidence-dir.",
    )
    parser.add_argument(
        "--gateway-base-url",
        default="http://127.0.0.1:5000",
        help="Docker gateway HTTP base URL used for live AI status.",
    )
    parser.add_argument("--mock", action="store_true")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Stop after N frames. Useful for local smoke tests.",
    )
    args = parser.parse_args(argv)

    return RunnerConfig(
        camera_index=args.camera_index,
        springboot_base_url=args.springboot_base_url,
        report_mode=args.report_mode,
        current_x=args.current_x,
        current_y=args.current_y,
        current_location_name=args.current_location_name,
        face_interval=max(1, args.face_interval),
        crack_cooldown=args.crack_cooldown,
        crack_enabled=not args.disable_crack,
        fall_cooldown=args.fall_cooldown,
        evidence_dir=args.evidence_dir,
        evidence_base_url=args.evidence_base_url,
        gateway_base_url=args.gateway_base_url,
        mock=args.mock,
        max_frames=args.max_frames,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv or sys.argv[1:])
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())

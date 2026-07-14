#!/usr/bin/env python3
"""
Realtime crack detector for the smart-car camera.

Run on Jetson:
  python3 crack_detect_camera.py --camera 0 --alert-score 900 --confirm-frames 4

Keys:
  q / ESC : quit
  s       : save current frame and ROI
"""

from __future__ import annotations

import argparse
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

import cv2
import numpy as np


DEFAULT_ALERT_SCORE = 900.0
DEFAULT_CONFIRM_FRAMES = 5
DEFAULT_HISTORY_SIZE = 6
DEFAULT_ROI_START = 0.45
DEFAULT_MIN_AREA = 70.0
DEFAULT_MIN_LENGTH = 45.0

_detection_lock = Lock()
_detection_history: deque[bool] = deque(maxlen=DEFAULT_HISTORY_SIZE)
_positive_frames: list[tuple[float, np.ndarray]] = []
_last_crack_evidence: np.ndarray | None = None
_marker_context = {
    "x": 0,
    "y": 0,
    "locationName": "",
}


@dataclass
class Candidate:
    x: int
    y: int
    w: int
    h: int
    area: float
    length: float
    width: float
    angle: float
    irregularity: float
    score: float
    is_tile_like: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime crack detection from camera.")
    parser.add_argument("--camera", type=int, default=0, help="Camera index, usually 0.")
    parser.add_argument("--alert-score", type=float, default=900, help="Single-frame crack score threshold.")
    parser.add_argument("--confirm-frames", type=int, default=5,
                        help="Positive frames required in each non-overlapping batch.")
    parser.add_argument("--history", type=int, default=6,
                        help="Number of recent frames used for confirmation.")
    parser.add_argument("--roi-start", type=float, default=0.45,
                        help="Start of ROI by image height. 0.45 means lower 55 percent.")
    parser.add_argument("--min-area", type=float, default=70, help="Minimum contour area.")
    parser.add_argument("--min-length", type=float, default=45, help="Minimum contour long side.")
    parser.add_argument("--width", type=int, default=0, help="Optional camera width.")
    parser.add_argument("--height", type=int, default=0, help="Optional camera height.")
    parser.add_argument("--save-dir", type=Path, default=Path("camera_captures"), help="Folder for saved frames.")
    parser.add_argument("--no-window", action="store_true", help="Do not show OpenCV windows.")
    return parser.parse_args()


def angle_from_rect(rect: tuple) -> float:
    (_, _), (rw, rh), angle = rect
    if rw < rh:
        angle += 90.0
    while angle <= -90:
        angle += 180
    while angle > 90:
        angle -= 180
    return angle


def make_dark_line_mask(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blur = cv2.GaussianBlur(enhanced, (5, 5), 0)

    mask = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        7,
    )

    open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    return mask


def contour_to_candidate(contour: np.ndarray, min_area: float, min_length: float) -> Candidate | None:
    area = float(cv2.contourArea(contour))
    if area < min_area:
        return None

    x, y, w, h = cv2.boundingRect(contour)
    length = float(max(w, h))
    width = float(min(w, h))
    if length < min_length:
        return None

    perimeter = float(cv2.arcLength(contour, True))
    irregularity = perimeter / (2.0 * (w + h) + 1.0)

    rect = cv2.minAreaRect(contour)
    (_, _), (rw, rh), _ = rect
    long_side = max(rw, rh)
    short_side = max(1.0, min(rw, rh))
    aspect = long_side / short_side
    angle = angle_from_rect(rect)

    is_tile_like = (
        length >= 140
        and width <= 10
        and aspect >= 10
        and irregularity <= 1.20
    )

    score = 0.0
    score += min(area, 900.0) * 0.10
    score += min(length, 300.0) * 0.75
    score += max(0.0, irregularity - 0.85) * 120.0

    if width >= 6:
        score += 18.0
    if aspect >= 5:
        score += 25.0
    if is_tile_like:
        score *= 0.25

    return Candidate(
        x=x,
        y=y,
        w=w,
        h=h,
        area=area,
        length=length,
        width=width,
        angle=angle,
        irregularity=irregularity,
        score=score,
        is_tile_like=is_tile_like,
    )


def detect_cracks(image: np.ndarray, min_area: float, min_length: float) -> tuple[list[Candidate], np.ndarray, float]:
    mask = make_dark_line_mask(image)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[Candidate] = []
    for contour in contours:
        candidate = contour_to_candidate(contour, min_area=min_area, min_length=min_length)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda c: c.score, reverse=True)
    useful = [c for c in candidates if not c.is_tile_like]
    image_score = sum(c.score for c in useful[:3])
    return candidates, mask, image_score


def update_crack_marker_context(x: int, y: int, location_name: str = "") -> None:
    """Update the App-map position used by subsequent crack markers."""
    with _detection_lock:
        _marker_context["x"] = int(x)
        _marker_context["y"] = int(y)
        _marker_context["locationName"] = str(location_name)


def reset_crack_history() -> None:
    """Clear temporal confirmation state, for example after changing cameras."""
    global _last_crack_evidence
    with _detection_lock:
        _detection_history.clear()
        _positive_frames.clear()
        _last_crack_evidence = None


def get_last_crack_evidence() -> np.ndarray | None:
    """Return a copy of the best positive frame from the last confirmed batch."""
    with _detection_lock:
        if _last_crack_evidence is None:
            return None
        return _last_crack_evidence.copy()


def detect_crack(frame: np.ndarray) -> dict | None:
    """Detect a crack in one OpenCV BGR frame and return a marker or None.

    The function does not open or control a camera. Calls are grouped into
    non-overlapping batches of six. A marker is returned on the sixth call
    only when more than four frames in that batch exceed the score threshold.
    """
    if frame is None:
        return None
    if not isinstance(frame, np.ndarray):
        raise TypeError("frame must be a numpy.ndarray")
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be a BGR image with shape (height, width, 3)")
    if frame.size == 0:
        return None

    height = frame.shape[0]
    roi_y = max(0, min(height - 1, int(height * DEFAULT_ROI_START)))
    roi = frame[roi_y:height, :]
    _, _, score = detect_cracks(
        roi,
        min_area=DEFAULT_MIN_AREA,
        min_length=DEFAULT_MIN_LENGTH,
    )
    frame_alarm = score >= DEFAULT_ALERT_SCORE

    global _last_crack_evidence
    with _detection_lock:
        _detection_history.append(frame_alarm)
        if frame_alarm:
            _positive_frames.append((score, frame.copy()))

        if len(_detection_history) < DEFAULT_HISTORY_SIZE:
            return None

        positive_count = sum(_detection_history)
        confirmed = positive_count > 4
        context = dict(_marker_context)
        if confirmed and _positive_frames:
            _, best_frame = max(_positive_frames, key=lambda item: item[0])
            _last_crack_evidence = best_frame.copy()
        else:
            _last_crack_evidence = None

        _detection_history.clear()
        _positive_frames.clear()

    if not confirmed:
        return None

    confidence = positive_count / DEFAULT_HISTORY_SIZE
    return {
        "type": "crack",
        "title": "检测到地面裂缝",
        "description": "小车检测到疑似地面裂缝",
        "x": context["x"],
        "y": context["y"],
        "level": "warning",
        "status": "unhandled",
        "source": "vision",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "locationName": context["locationName"],
        "confidence": round(confidence, 4),
    }


def draw_result(roi: np.ndarray, candidates: list[Candidate], score: float, frame_alarm: bool, confirmed_alarm: bool) -> np.ndarray:
    out = roi.copy()
    if confirmed_alarm:
        title = f"CRACK ALARM score={score:.0f}"
        color = (0, 0, 255)
    elif frame_alarm:
        title = f"SUSPECT score={score:.0f}"
        color = (0, 180, 255)
    else:
        title = f"NORMAL score={score:.0f}"
        color = (0, 160, 0)

    cv2.putText(out, title, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    for c in candidates[:8]:
        box_color = (255, 170, 0) if c.is_tile_like else (0, 0, 255)
        label = f"{'tile' if c.is_tile_like else 'cand'} {c.score:.0f}"
        cv2.rectangle(out, (c.x, c.y), (c.x + c.w, c.y + c.h), box_color, 2)
        cv2.putText(out, label, (c.x, max(18, c.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 1)

    return out


def main() -> int:
    args = parse_args()
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"ERROR: cannot open camera {args.camera}")
        return 1

    if args.width > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height > 0:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    args.save_dir.mkdir(parents=True, exist_ok=True)
    history: deque[bool] = deque(maxlen=max(args.history, args.confirm_frames))
    last_print_time = 0.0
    saved_index = 0

    print("Camera started.")
    print("Press q or ESC to quit. Press s to save current frame.")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("WARN: failed to read frame")
            time.sleep(0.1)
            continue

        h, w = frame.shape[:2]
        roi_y = int(h * args.roi_start)
        roi = frame[roi_y:h, :]

        candidates, mask, score = detect_cracks(roi, min_area=args.min_area, min_length=args.min_length)
        frame_alarm = score >= args.alert_score
        history.append(frame_alarm)
        batch_complete = len(history) >= args.history
        positive_count = sum(history) if batch_complete else 0
        confirmed_alarm = (
            batch_complete
            and positive_count >= args.confirm_frames
        )

        if batch_complete:
            print(
                f"batch result: {'CRACK_ALARM' if confirmed_alarm else 'NORMAL'} "
                f"positive={positive_count}/{args.history}"
            )
            history.clear()

        annotated_roi = draw_result(roi, candidates, score, frame_alarm, confirmed_alarm)
        display = frame.copy()
        display[roi_y:h, :] = annotated_roi
        cv2.rectangle(display, (0, roi_y), (w - 1, h - 1), (0, 255, 255), 2)

        now = time.time()
        if now - last_print_time > 1.0:
            print(
                f"{'CRACK_ALARM' if confirmed_alarm else 'NORMAL'} "
                f"score={score:.1f} batch={sum(history)}/{len(history)}"
            )
            last_print_time = now

        if not args.no_window:
            cv2.imshow("crack_detector", display)
            cv2.imshow("crack_roi_mask", mask)
            key = cv2.waitKey(1) & 0xFF
        else:
            key = 255

        if key in (27, ord("q")):
            break
        if key == ord("s"):
            saved_index += 1
            cv2.imwrite(str(args.save_dir / f"frame_{saved_index:03d}.jpg"), frame)
            cv2.imwrite(str(args.save_dir / f"roi_{saved_index:03d}.jpg"), roi)
            cv2.imwrite(str(args.save_dir / f"mask_{saved_index:03d}.jpg"), mask)
            print(f"saved sample {saved_index} to {args.save_dir}")

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

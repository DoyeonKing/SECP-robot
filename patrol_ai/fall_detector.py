from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent


def _discover_yolo_root() -> Path:
  candidates: list[Path] = []
  env_root = os.getenv("FALL_DETECT_YOLO_ROOT", "").strip()
  if env_root:
    candidates.append(Path(env_root).expanduser())
  candidates.extend(
    [
      ROOT,
      ROOT.parent / "yolov5-7.0",
      Path("/home/jetson/yolov5-7.0"),
    ]
  )

  for candidate in candidates:
    if (
      (candidate / "models" / "common.py").exists()
      and (candidate / "utils" / "general.py").exists()
    ):
      return candidate
  return ROOT


YOLO_ROOT = _discover_yolo_root()

for path in (ROOT, YOLO_ROOT):
  path_str = str(path)
  if path_str not in sys.path:
    sys.path.append(path_str)

from fall_detect import (  # type: ignore
  PERSON_CLASS_ID,
  FallState,
  build_ai_result,
  choose_largest_person,
  compute_aspect_ratio,
  is_low_posture,
)

import torch
from models.common import DetectMultiBackend  # type: ignore
from utils.augmentations import letterbox  # type: ignore
from utils.general import check_img_size, non_max_suppression, scale_boxes  # type: ignore
from utils.torch_utils import select_device  # type: ignore


DEFAULT_WEIGHTS = "yolov5s.pt"
DEFAULT_DATA = "data/coco128.yaml"
DEFAULT_IMGSZ = (640, 640)
DEFAULT_CONF_THRES = 0.25
DEFAULT_IOU_THRES = 0.45
DEFAULT_MAX_DET = 1000
DEFAULT_PERSON_CONF = 0.50
DEFAULT_FALL_RATIO = 1.30
# Keep posture thresholds aligned with the car-side script, but shorten the
# confirmation window so brief fall-like segments can still trigger in demos.
DEFAULT_FALL_DURATION = 1.20
DEFAULT_LOW_CENTER_Y = 0.55
DEFAULT_LOW_BOTTOM_Y = 0.68
DEFAULT_LOW_MAX_HEIGHT = 0.75
DEFAULT_LOW_MIN_RATIO = 0.35

_DEFAULT_DETECTOR: "FallFrameDetector | None" = None


def _validate_frame(frame: np.ndarray) -> None:
  if not isinstance(frame, np.ndarray):
    raise TypeError("frame must be a numpy.ndarray in BGR format.")
  if frame.size == 0:
    raise ValueError("frame must not be empty.")
  if frame.ndim != 3:
    raise ValueError("frame must be a 3D numpy.ndarray in BGR format.")
  if frame.shape[2] < 3:
    raise ValueError("frame must have at least 3 channels in BGR format.")


def _resolve_runtime_path(path_value: str) -> str:
  path = Path(path_value)
  if path.is_absolute():
    return str(path)

  local_candidate = ROOT / path
  if local_candidate.exists():
    return str(local_candidate)

  yolo_candidate = YOLO_ROOT / path
  if yolo_candidate.exists():
    return str(yolo_candidate)

  return str(path)


class FallFrameDetector:
  def __init__(
    self,
    *,
    weights: str = DEFAULT_WEIGHTS,
    data: str = DEFAULT_DATA,
    imgsz: tuple[int, int] = DEFAULT_IMGSZ,
    conf_thres: float = DEFAULT_CONF_THRES,
    iou_thres: float = DEFAULT_IOU_THRES,
    max_det: int = DEFAULT_MAX_DET,
    device: str = "",
    half: bool = False,
    dnn: bool = False,
    person_conf: float = DEFAULT_PERSON_CONF,
    fall_ratio: float = DEFAULT_FALL_RATIO,
    fall_duration: float = DEFAULT_FALL_DURATION,
    low_center_y: float = DEFAULT_LOW_CENTER_Y,
    low_bottom_y: float = DEFAULT_LOW_BOTTOM_Y,
    low_max_height: float = DEFAULT_LOW_MAX_HEIGHT,
    low_min_ratio: float = DEFAULT_LOW_MIN_RATIO,
  ) -> None:
    weights = _resolve_runtime_path(weights)
    data = _resolve_runtime_path(data)
    self.conf_thres = float(conf_thres)
    self.iou_thres = float(iou_thres)
    self.max_det = int(max_det)
    self.person_conf = float(person_conf)
    self.low_center_y = float(low_center_y)
    self.low_bottom_y = float(low_bottom_y)
    self.low_max_height = float(low_max_height)
    self.low_min_ratio = float(low_min_ratio)
    self.state = FallState(fall_ratio, fall_duration)
    self._reset_evidence_state(clear_last=True)

    self.device = select_device(device)
    self.model = DetectMultiBackend(
      weights,
      device=self.device,
      dnn=dnn,
      data=data,
      fp16=half,
    )
    self.stride = self.model.stride
    self.pt = self.model.pt
    checked_size = check_img_size(imgsz, s=self.stride)
    if isinstance(checked_size, int):
      checked_size = (checked_size, checked_size)
    self.imgsz = tuple(checked_size)
    self.model.warmup(imgsz=(1 if self.pt else 1, 3, *self.imgsz))

  def reset_state(self) -> None:
    self.state.abnormal_since = None
    self.state.abnormal_reason = "normal"
    self._reset_evidence_state(clear_last=True)

  def _reset_evidence_state(self, *, clear_last: bool) -> None:
    if clear_last:
      self._last_fall_evidence_frame: np.ndarray | None = None
      self._last_fall_evidence_score = 0.0
      self._last_fall_evidence_timestamp: float | None = None
    self._candidate_fall_evidence_frame: np.ndarray | None = None
    self._candidate_fall_score = 0.0
    self._candidate_fall_timestamp: float | None = None
    self._last_fall_emitted = False

  def _is_positive_candidate(
    self,
    person: dict[str, Any] | None,
    aspect_ratio: float | None,
    low_posture: bool,
  ) -> bool:
    return bool(
      person is not None
      and (
        low_posture
        or (
          aspect_ratio is not None
          and float(aspect_ratio) >= float(self.state.ratio_threshold)
        )
      )
    )

  def _compute_candidate_score(
    self,
    person: dict[str, Any],
    frame_shape: tuple[int, ...],
  ) -> float:
    frame_h = max(1.0, float(frame_shape[0]))
    frame_w = max(1.0, float(frame_shape[1]))
    x1, y1, x2, y2 = [float(v) for v in person["bbox"]]
    bbox_w = max(0.0, x2 - x1)
    bbox_h = max(0.0, y2 - y1)
    bbox_area_ratio = (bbox_w * bbox_h) / (frame_h * frame_w)
    return float(person["confidence"]) * bbox_area_ratio

  def _update_evidence_cache(
    self,
    frame: np.ndarray,
    person: dict[str, Any] | None,
    aspect_ratio: float | None,
    low_posture: bool,
    fall_alert: bool,
    now: float,
  ) -> None:
    is_positive_candidate = self._is_positive_candidate(person, aspect_ratio, low_posture)
    if not is_positive_candidate:
      if self._last_fall_emitted and not fall_alert:
        self._last_fall_emitted = False
      self._reset_evidence_state(clear_last=False)
      return

    assert person is not None
    score = self._compute_candidate_score(person, frame.shape)
    if (
      self._candidate_fall_evidence_frame is None
      or score > self._candidate_fall_score
      or (
        score == self._candidate_fall_score
        and (
          self._candidate_fall_timestamp is None
          or float(now) >= self._candidate_fall_timestamp
        )
      )
    ):
      self._candidate_fall_evidence_frame = frame.copy()
      self._candidate_fall_score = score
      self._candidate_fall_timestamp = float(now)

    if fall_alert and self._candidate_fall_evidence_frame is not None:
      should_commit = (
        not self._last_fall_emitted
        or self._last_fall_evidence_frame is None
        or self._candidate_fall_score > self._last_fall_evidence_score
        or (
          self._candidate_fall_score == self._last_fall_evidence_score
          and (
            self._last_fall_evidence_timestamp is None
            or (
              self._candidate_fall_timestamp is not None
              and self._candidate_fall_timestamp >= self._last_fall_evidence_timestamp
            )
          )
        )
      )
      if should_commit:
        self._last_fall_evidence_frame = self._candidate_fall_evidence_frame.copy()
        self._last_fall_evidence_score = self._candidate_fall_score
        self._last_fall_evidence_timestamp = self._candidate_fall_timestamp
      self._last_fall_emitted = True
    elif not fall_alert:
      self._last_fall_emitted = False

  def get_last_fall_evidence(self) -> np.ndarray | None:
    if self._last_fall_evidence_frame is None:
      return None
    return self._last_fall_evidence_frame.copy()

  def _preprocess(self, frame: np.ndarray) -> torch.Tensor:
    resized = letterbox(frame, self.imgsz, stride=self.stride, auto=self.pt)[0]
    resized = resized.transpose((2, 0, 1))[::-1]
    resized = np.ascontiguousarray(resized)

    tensor = torch.from_numpy(resized).to(self.device)
    tensor = tensor.half() if self.model.fp16 else tensor.float()
    tensor /= 255.0
    if len(tensor.shape) == 3:
      tensor = tensor[None]
    return tensor

  def detect_result(self, frame: np.ndarray, now: float | None = None) -> dict[str, Any]:
    _validate_frame(frame)
    if now is None:
      now = time.time()

    im0 = frame.copy()
    im = self._preprocess(im0)
    pred = self.model(im, augment=False, visualize=False)
    pred = non_max_suppression(
      pred,
      self.conf_thres,
      self.iou_thres,
      [PERSON_CLASS_ID],
      False,
      max_det=self.max_det,
    )

    detections: list[dict[str, Any]] = []
    det = pred[0]
    if len(det):
      det[:, :4] = scale_boxes(im.shape[2:], det[:, :4], im0.shape).round()
      for *xyxy, conf, cls in reversed(det):
        detections.append(
          {
            "class_id": int(cls),
            "confidence": float(conf),
            "bbox": [float(v) for v in xyxy],
          }
        )

    person = choose_largest_person(detections, self.person_conf)
    aspect_ratio = compute_aspect_ratio(person["bbox"]) if person else None
    low_posture = (
      is_low_posture(
        person["bbox"],
        im0.shape,
        center_y_threshold=self.low_center_y,
        bottom_y_threshold=self.low_bottom_y,
        max_height_ratio=self.low_max_height,
        min_aspect_ratio=self.low_min_ratio,
      )
      if person
      else False
    )
    fall_alert = self.state.update(now, person is not None, aspect_ratio, low_posture)
    self._update_evidence_cache(im0, person, aspect_ratio, low_posture, fall_alert, float(now))
    return build_ai_result(person, aspect_ratio, low_posture, fall_alert, self.state, now)

  def detect(self, frame: np.ndarray, now: float | None = None) -> dict[str, Any]:
    result = self.detect_result(frame, now=now)
    return {
      "fall_alert": bool(result["fall_alert"]),
      "risk_level": "high" if bool(result["fall_alert"]) else "low",
    }


def _get_default_detector() -> FallFrameDetector:
  global _DEFAULT_DETECTOR
  if _DEFAULT_DETECTOR is None:
    _DEFAULT_DETECTOR = FallFrameDetector()
  return _DEFAULT_DETECTOR


def reset_fall_detector_state() -> None:
  if _DEFAULT_DETECTOR is not None:
    _DEFAULT_DETECTOR.reset_state()


def detect_fall(frame: np.ndarray) -> dict[str, Any]:
  return _get_default_detector().detect(frame)


def get_last_fall_evidence() -> np.ndarray | None:
  if _DEFAULT_DETECTOR is None:
    return None
  return _DEFAULT_DETECTOR.get_last_fall_evidence()


__all__ = [
  "FallFrameDetector",
  "detect_fall",
  "get_last_fall_evidence",
  "reset_fall_detector_state",
]

from __future__ import annotations

from typing import Any

import numpy as np

from face_common import classify_faces, init_face_app, load_gallery


DEFAULT_THRESHOLD = 0.45

_FACE_APP = None
_GALLERY: dict[str, dict[str, Any]] | None = None


def _validate_frame(frame: np.ndarray) -> None:
  if not isinstance(frame, np.ndarray):
    raise TypeError("frame must be a numpy.ndarray in BGR format.")
  if frame.size == 0:
    raise ValueError("frame must not be empty.")
  if frame.ndim != 3:
    raise ValueError("frame must be a 3D numpy.ndarray in BGR format.")
  if frame.shape[2] < 3:
    raise ValueError("frame must have at least 3 channels in BGR format.")


def _get_face_app():
  global _FACE_APP
  if _FACE_APP is None:
    _FACE_APP = init_face_app()
  return _FACE_APP


def _get_gallery() -> dict[str, dict[str, Any]]:
  global _GALLERY
  if _GALLERY is None:
    _GALLERY = load_gallery()
  if not _GALLERY:
    raise RuntimeError("No embeddings found. Run build_face_db.py first.")
  return _GALLERY


def reload_recognizer_resources() -> None:
  global _FACE_APP, _GALLERY
  _FACE_APP = None
  _GALLERY = None


def recognize_face_with_faces(
  frame: np.ndarray,
  threshold: float = DEFAULT_THRESHOLD,
) -> tuple[dict[str, Any], list[Any]]:
  _validate_frame(frame)
  app = _get_face_app()
  gallery = _get_gallery()
  faces = app.get(frame)
  payload = classify_faces(faces, gallery, threshold=threshold).to_payload()
  return payload, faces


def recognize_face(
  frame: np.ndarray,
  threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
  payload, _ = recognize_face_with_faces(frame, threshold=threshold)
  return payload


__all__ = [
  "DEFAULT_THRESHOLD",
  "recognize_face",
  "recognize_face_with_faces",
  "reload_recognizer_resources",
]

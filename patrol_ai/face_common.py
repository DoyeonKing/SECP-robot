from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from insightface.app import FaceAnalysis


SCRIPT_DIR = Path(__file__).resolve().parent
FACE_DB_ROOT = SCRIPT_DIR.parent
IDENTITY_MAP_PATH = SCRIPT_DIR / "identity_map.json"
DEFAULT_EMBEDDING_DIR = FACE_DB_ROOT / "embeddings"
DEFAULT_IMAGES_DIR = FACE_DB_ROOT / "images"
DEFAULT_LATEST_JSON = FACE_DB_ROOT / "face_recognition_latest.json"
DEFAULT_JSONL = FACE_DB_ROOT / "face_recognition.jsonl"

CN_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Identity:
  elder_profile_id: str
  elder_code: str
  display_name: str


@dataclass
class MatchResult:
  status: str
  elder_profile_id: str
  elder_code: str
  similarity: float
  bbox: list[int]
  face_count: int
  ts: str

  def to_payload(self) -> dict[str, Any]:
    return {
      "status": self.status,
      "elderProfileId": self.elder_profile_id,
      "elderCode": self.elder_code,
      "similarity": round(float(self.similarity), 4),
      "bbox": self.bbox,
      "face_count": int(self.face_count),
      "ts": self.ts,
    }


def now_iso() -> str:
  return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_identity_map(path: Path = IDENTITY_MAP_PATH) -> dict[str, Identity]:
  raw = json.loads(path.read_text(encoding="utf-8"))
  identity_map: dict[str, Identity] = {}
  for elder_code, data in raw.items():
    identity_map[elder_code] = Identity(
      elder_profile_id=str(data["elderProfileId"]),
      elder_code=str(data.get("elderCode", elder_code)),
      display_name=str(data.get("displayName", elder_code)),
    )
  return identity_map


def init_face_app(det_size: tuple[int, int] = (640, 640)) -> FaceAnalysis:
  app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
  app.prepare(ctx_id=0, det_size=det_size)
  return app


def l2_normalize(vector: np.ndarray) -> np.ndarray:
  vector = vector.astype(np.float32)
  norm = np.linalg.norm(vector)
  if norm <= 1e-12:
    return vector
  return vector / norm


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
  return float(np.dot(l2_normalize(left), l2_normalize(right)))


def load_gallery(
  embedding_dir: Path = DEFAULT_EMBEDDING_DIR,
  identity_map: dict[str, Identity] | None = None,
) -> dict[str, dict[str, Any]]:
  if identity_map is None:
    identity_map = load_identity_map()

  gallery: dict[str, dict[str, Any]] = {}
  for elder_code, identity in identity_map.items():
    embedding_path = embedding_dir / f"{elder_code}.npy"
    if not embedding_path.exists():
      continue
    gallery[elder_code] = {
      "identity": identity,
      "embedding": l2_normalize(np.load(embedding_path)),
    }
  return gallery


def pick_largest_face(faces: list[Any]) -> Any | None:
  if not faces:
    return None

  def area(face: Any) -> float:
    x1, y1, x2, y2 = face.bbox.astype(int).tolist()
    return float(max(0, x2 - x1) * max(0, y2 - y1))

  return max(faces, key=area)


def match_embedding(
  embedding: np.ndarray,
  gallery: dict[str, dict[str, Any]],
) -> tuple[Identity | None, float]:
  best_identity: Identity | None = None
  best_score = -1.0
  probe = l2_normalize(embedding)

  for item in gallery.values():
    score = cosine_similarity(probe, item["embedding"])
    if score > best_score:
      best_score = score
      best_identity = item["identity"]

  return best_identity, best_score


def classify_faces(
  faces: list[Any],
  gallery: dict[str, dict[str, Any]],
  threshold: float,
) -> MatchResult:
  ts = now_iso()
  if not faces:
    return MatchResult("no_face", "", "", 0.0, [], 0, ts)

  if len(faces) > 1:
    return MatchResult("multiple_faces", "", "", 0.0, [], len(faces), ts)

  face = faces[0]
  bbox = face.bbox.astype(int).tolist()
  identity, score = match_embedding(face.embedding, gallery)
  if identity is not None and score >= threshold:
    return MatchResult(
      "matched",
      identity.elder_profile_id,
      identity.elder_code,
      score,
      bbox,
      1,
      ts,
    )

  return MatchResult("unknown_face", "", "", score, bbox, 1, ts)


def write_latest_json(payload: dict[str, Any], output_path: Path = DEFAULT_LATEST_JSON) -> None:
  output_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )


def append_jsonl(payload: dict[str, Any], output_path: Path = DEFAULT_JSONL) -> None:
  with output_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

#!/usr/bin/env python3
"""Offline repository policy and snapshot-history verification."""

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_COMMIT = "8ffd2315b23194f89b660f35305a477f8ba4c008"
SNAPSHOT_DOCS = "docs/snapshot-20260714"
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024
APPROVED_WORKFLOW_HASHES = {
    ".github/workflows/ci.yml": "f7f38bad10be40532f83e3c77e03dca274f5f1992337119d866a5cfc2ceba973",
}
APPROVED_ACTIONS = {
    "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
}

FORBIDDEN_DIRECTORIES = {
    "__pycache__",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "build",
    "camera_captures",
    "embeddings",
    "evidence",
    "face_db",
    "facedb",
    "faces",
    "install",
    "log",
    "logs",
    "node_modules",
}

FORBIDDEN_SUFFIXES = {
    ".7z",
    ".avi",
    ".bag",
    ".bak",
    ".bin",
    ".bmp",
    ".bz2",
    ".caffemodel",
    ".ckpt",
    ".copy",
    ".csv",
    ".db",
    ".db3",
    ".deb",
    ".dll",
    ".engine",
    ".exe",
    ".feather",
    ".gif",
    ".gz",
    ".h5",
    ".hdf5",
    ".jpg",
    ".jpeg",
    ".jsonl",
    ".keras",
    ".key",
    ".las",
    ".log",
    ".mcap",
    ".mkv",
    ".mlmodel",
    ".mov",
    ".mp3",
    ".mp4",
    ".npy",
    ".npz",
    ".onnx",
    ".p12",
    ".parquet",
    ".pb",
    ".pcap",
    ".pcd",
    ".pem",
    ".pfx",
    ".pgm",
    ".ply",
    ".png",
    ".pt",
    ".pth",
    ".pyc",
    ".pyo",
    ".rar",
    ".safetensors",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".svg",
    ".tar",
    ".tgz",
    ".tflite",
    ".trt",
    ".uff",
    ".wav",
    ".webp",
    ".weights",
    ".whl",
    ".xz",
    ".zip",
}

REQUIRED_GITIGNORE_RULES = {
    ".env",
    ".env.*",
    "!.env.example",
    "evidence/",
    "face_db/",
    "embeddings/",
    "identity_map.json",
    "*.pt",
    "*.onnx",
    "*.tflite",
    "*.safetensors",
    "*.jpg",
    "*.pgm",
    "*.wav",
    "*.db3",
    "*.mcap",
    "*.pcd",
    "*.zip",
    "build/",
    "install/",
    "log/",
    "*.pem",
    "*.key",
    "secrets.env",
    "**/maps/*.yaml",
}

IGNORE_SAMPLES = (
    ".env",
    ".env.local",
    "secrets.env",
    "evidence/event.jpg",
    "face_db/person.npy",
    "patrol_ai/yolov5s.pt",
    "gateway/song.wav",
    "ros2/inspection_map_bridge/maps/site.pgm",
    "ros2/inspection_map_bridge/maps/site.yaml",
    "deploy/maps/site.yaml",
    "recording/map.db3",
    "recording/map.mcap",
    "cloud/map.pcd",
    "model/model.tflite",
    "model/model.safetensors",
    "archive/source.zip",
    "build/output.bin",
    "install/setup.sh",
    "log/runtime.txt",
)

IMMUTABLE_SNAPSHOT_RECORDS = (
    "SHA256SUMS",
    SNAPSHOT_DOCS + "/SHA256SUMS",
    SNAPSHOT_DOCS + "/excluded_files.md",
    SNAPSHOT_DOCS + "/file_manifest.txt",
    SNAPSHOT_DOCS + "/runtime_environment.md",
    SNAPSHOT_DOCS + "/source_locations.md",
)

PRIVATE_KEY_RE = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
URL_CREDENTIAL_RE = re.compile(rb"(?i)://[^/\s:@]+:[^@\s]+@")
GITHUB_TOKEN_RE = re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
GITHUB_FINE_GRAINED_TOKEN_RE = re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
AWS_ACCESS_KEY_RE = re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
HUAWEI_CREDENTIAL_RE = re.compile(
    rb"(?i)\b(?:HUAWEI(?:CLOUD)?|HW)[_-]?(?:ACCESS|SECRET)[_-]?KEY\b"
    rb"\s*[:=]\s*[\"']?[A-Za-z0-9/+_=.-]{8,}"
)
HUAWEICLOUD_SDK_CREDENTIAL_RE = re.compile(
    rb"(?im)^\s*HUAWEICLOUD_SDK_(?:AK|SK)\s*[:=]\s*"
    rb"(?P<value>[^#\r\n]*)"
)
SECRET_ASSIGNMENT_RE = re.compile(
    rb"(?i)[\"']?\b(?:password|passwd|pwd|token|secret|cookie|api[_-]?key|"
    rb"access[_-]?key|secret[_-]?key)\b[\"']?\s*[:=]\s*"
    rb"(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)"
)
UNQUOTED_SECRET_RE = re.compile(
    rb"(?im)^\s*(?:password|passwd|pwd|api[_-]?key|access[_-]?key|"
    rb"secret[_-]?key|auth[_-]?token|bearer[_-]?token|token|cookie)\s*"
    rb"(?::(?![=])|=(?!=))\s*(?P<value>[^#\r\n]*)"
)
IDENTITY_LITERAL_RE = re.compile(
    rb"(?i)[\"'](?:elder(?:ProfileId|Code|Name)|phone|mobile|idCard)[\"']"
    rb"\s*:\s*"
    rb"[\"'][^\"'\r\n]+[\"']"
)
UNQUOTED_IDENTITY_RE = re.compile(
    rb"(?im)^\s*(?:elder(?:ProfileId|Code|Name)|phone|mobile|idCard)\s*:\s*"
    rb"(?![\"']|\s*$|null\b|none\b|\$\{\{)[^#\r\n]+"
)
LONG_BASE64_RE = re.compile(
    rb"(?m)^[ \t]*[A-Za-z0-9+/_-]{4096,}={0,2}[ \t]*$"
)
BASE64_LINE_RE = re.compile(rb"^[A-Za-z0-9+/_-]+={0,2}$")
MAP_YAML_KEY_RE = re.compile(
    rb"(?im)(?:^|[,{])\s*[\"']?"
    rb"(image|resolution|origin|occupied_thresh|free_thresh|negate)"
    rb"[\"']?\s*:"
)
SAFE_CONFIG_VALUE_RE = re.compile(
    rb"(?ix)^(?:"
    rb"null|none|~|"
    rb"\$\{\{\s*(?:secrets|env|vars)\.[A-Z_][A-Z0-9_]*\s*\}\}|"
    rb"\$\{[A-Z_][A-Z0-9_]*\}|"
    rb"\$env:[A-Z_][A-Z0-9_]*|\$[A-Z_][A-Z0-9_]*|"
    rb"%[A-Z_][A-Z0-9_]*%|@[A-Z_][A-Z0-9_]*@|"
    rb"\{\{\s*[A-Z_][A-Z0-9_.]*\s*\}\}|"
    rb"<(?:(?:set|change|replace)[_-]?me|placeholder|redacted|"
    rb"your[_-][A-Z0-9_-]+)>|"
    rb"(?:change|replace)[_-]?me|placeholder|redacted|example|"
    rb"your[_-][A-Z0-9_-]+|"
    rb"(?:(?:os\.)?getenv|system\.getenv)\s*\(\s*[\"']"
    rb"[A-Z_][A-Z0-9_]*[\"']\s*\)|"
    rb"os\.environ(?:\.get\s*\(\s*[\"'][A-Z_][A-Z0-9_]*[\"']\s*\)|"
    rb"\s*\[\s*[\"'][A-Z_][A-Z0-9_]*[\"']\s*\])|"
    rb"env(?:iron)?(?:\.get\s*\(\s*[\"'][A-Z_][A-Z0-9_]*[\"']\s*\)|"
    rb"\s*\[\s*[\"'][A-Z_][A-Z0-9_]*[\"']\s*\]|\.[A-Z_][A-Z0-9_]*)|"
    rb"process\.env(?:\.[A-Z_][A-Z0-9_]*|"
    rb"\s*\[\s*[\"'][A-Z_][A-Z0-9_]*[\"']\s*\])"
    rb")$"
)


def git_bytes(*args: str, check: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("git {} failed: {}".format(" ".join(args), message))
    return completed.stdout


def tracked_paths() -> List[Path]:
    output = git_bytes("ls-files", "-z")
    return [
        Path(item.decode("utf-8"))
        for item in output.split(b"\0")
        if item
    ]


def tracked_modes() -> Dict[str, str]:
    output = git_bytes("ls-files", "-s", "-z")
    modes: Dict[str, str] = {}
    for item in output.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0].decode("ascii")
        modes[raw_path.decode("utf-8")] = mode
    return modes


def git_blob(commit: str, path: str) -> bytes:
    return git_bytes("show", "{}:{}".format(commit, path))


def git_index_blob(path: str) -> bytes:
    return git_bytes("show", ":{}".format(path))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_map_yaml(path: Path, data: bytes) -> bool:
    if path.suffix.casefold() not in {".yaml", ".yml"}:
        return False
    if "maps" in {part.casefold() for part in path.parts}:
        return True
    keys = {match.group(1).lower() for match in MAP_YAML_KEY_RE.finditer(data)}
    return {b"image", b"resolution", b"origin"}.issubset(keys)


def has_long_base64_payload(data: bytes) -> bool:
    if LONG_BASE64_RE.search(data):
        return True

    encoded_bytes = 0
    for raw_line in data.splitlines():
        line = raw_line.strip()
        if len(line) < 16 or not BASE64_LINE_RE.fullmatch(line):
            encoded_bytes = 0
            continue
        encoded_bytes += len(line.rstrip(b"="))
        if encoded_bytes >= 4096:
            return True
        if line.endswith(b"="):
            encoded_bytes = 0
    return False


def is_safe_config_value(raw_value: bytes) -> bool:
    value = raw_value.strip()
    if not value:
        return True
    if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {b"'", b'"'}:
        value = value[1:-1].strip()
        if not value:
            return True
    return SAFE_CONFIG_VALUE_RE.fullmatch(value) is not None


def has_huawei_sdk_credential(data: bytes) -> bool:
    return any(
        not is_safe_config_value(match.group("value"))
        for match in HUAWEICLOUD_SDK_CREDENTIAL_RE.finditer(data)
    )


def has_unquoted_secret_assignment(data: bytes) -> bool:
    for match in UNQUOTED_SECRET_RE.finditer(data):
        value = match.group("value").strip()
        if value.startswith((b"'", b'"')):
            continue
        if not is_safe_config_value(value):
            return True
    return False


def has_quoted_secret_assignment(data: bytes) -> bool:
    for match in SECRET_ASSIGNMENT_RE.finditer(data):
        if not is_safe_config_value(match.group("value")):
            return True
        line_tail = data[match.end():].split(b"\n", 1)[0].strip()
        if line_tail and not line_tail.startswith((b"#", b",", b"}", b"]", b")")):
            return True
    return False


def append_path_policy_errors(
    path: Path,
    size: int,
    mode: str,
    context: str,
    errors: List[str],
) -> None:
    relative = path.as_posix()
    prefix = "{}: ".format(context) if context else ""
    if mode in {"120000", "160000"}:
        errors.append(prefix + "links and submodules are forbidden: " + relative)

    lowered_parts = {part.casefold() for part in path.parts}
    if lowered_parts.intersection(FORBIDDEN_DIRECTORIES):
        errors.append(prefix + "forbidden directory in tracked path: " + relative)
    if "maps" in {part.casefold() for part in path.parts[:-1]} and path.name.casefold() != "readme.md":
        errors.append(prefix + "files under maps directories are forbidden: " + relative)

    lowered_name = path.name.casefold()
    if lowered_name != ".env.example" and (
        lowered_name == ".env"
        or lowered_name.startswith(".env.")
        or lowered_name.endswith(".env")
    ):
        errors.append(prefix + "real environment file is forbidden: " + relative)
    if lowered_name == "identity_map.json":
        errors.append(prefix + "identity mapping is forbidden: " + relative)

    lowered_path = relative.casefold()
    if lowered_path.endswith(".tar.gz") or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        errors.append(prefix + "forbidden asset or generated file: " + relative)
    if is_map_yaml(path, b""):
        errors.append(prefix + "site map YAML is forbidden: " + relative)
    if size > MAX_TRACKED_FILE_BYTES:
        errors.append(prefix + "tracked file exceeds 5 MiB: " + relative)


def append_blob_policy_errors(
    path: Path,
    data: bytes,
    context: str,
    errors: List[str],
) -> bool:
    relative = path.as_posix()
    prefix = "{}: ".format(context) if context else ""
    if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
        errors.append(prefix + "Git LFS pointers are forbidden: " + relative)
    if b"\0" in data:
        errors.append(prefix + "binary tracked file is forbidden: " + relative)
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(prefix + "tracked text is not UTF-8: " + relative)
        return False
    if is_map_yaml(path, data):
        errors.append(prefix + "occupancy-map YAML content is forbidden: " + relative)
    if has_long_base64_payload(data):
        errors.append(prefix + "long base64 payload is forbidden: " + relative)

    patterns = (
        (PRIVATE_KEY_RE, "private key"),
        (URL_CREDENTIAL_RE, "URL credentials"),
        (GITHUB_TOKEN_RE, "GitHub token"),
        (GITHUB_FINE_GRAINED_TOKEN_RE, "fine-grained GitHub token"),
        (AWS_ACCESS_KEY_RE, "cloud access key"),
        (HUAWEI_CREDENTIAL_RE, "Huawei Cloud credential"),
        (IDENTITY_LITERAL_RE, "hardcoded personal identity"),
        (UNQUOTED_IDENTITY_RE, "unquoted personal identity"),
    )
    for pattern, description in patterns:
        if pattern.search(data):
            errors.append(prefix + "{} detected in {}".format(description, relative))
    if has_huawei_sdk_credential(data):
        errors.append(prefix + "Huawei Cloud SDK credential detected in " + relative)
    if has_quoted_secret_assignment(data):
        errors.append(prefix + "hardcoded secret assignment detected in " + relative)
    if has_unquoted_secret_assignment(data):
        errors.append(prefix + "unquoted secret assignment detected in " + relative)
    return True


def parse_sha256_file(data: bytes, label: str) -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    seen = set()
    for line_number, raw_line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not raw_line:
            continue
        parts = raw_line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValueError("{}:{} has invalid SHA256 format".format(label, line_number))
        if parts[1] in seen:
            raise ValueError("{} contains duplicate path {}".format(label, parts[1]))
        seen.add(parts[1])
        entries.append((parts[0], parts[1]))
    return entries


def parse_payload_manifest(data: bytes) -> List[Tuple[str, int, str]]:
    entries: List[Tuple[str, int, str]] = []
    seen = set()
    for line_number, raw_line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        parts = raw_line.split("  ", 2)
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValueError("file_manifest.txt:{} has invalid format".format(line_number))
        if parts[2] in seen:
            raise ValueError("file_manifest.txt contains duplicate path {}".format(parts[2]))
        seen.add(parts[2])
        entries.append((parts[0], int(parts[1]), parts[2]))
    return entries


def snapshot_path_to_repo(path: str) -> str:
    prefix = "manifests/"
    if path.startswith(prefix):
        return SNAPSHOT_DOCS + "/" + path[len(prefix):]
    return path


def verify_snapshot_history(errors: List[str]) -> None:
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", SNAPSHOT_COMMIT, "HEAD"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        errors.append("initial sanitized snapshot commit is not an ancestor of HEAD")
        return

    subject = git_bytes("show", "-s", "--format=%s", SNAPSHOT_COMMIT).decode().strip()
    if subject != "import: add sanitized robot source snapshot":
        errors.append("initial snapshot commit message changed or is unavailable")

    try:
        root_entries = parse_sha256_file(
            git_blob(SNAPSHOT_COMMIT, "SHA256SUMS"),
            "initial SHA256SUMS",
        )
        for expected, path in root_entries:
            actual = sha256(git_blob(SNAPSHOT_COMMIT, path))
            if actual != expected:
                errors.append("initial commit checksum mismatch: {}".format(path))

        snapshot_sum_path = SNAPSHOT_DOCS + "/SHA256SUMS"
        snapshot_entries = parse_sha256_file(
            git_blob(SNAPSHOT_COMMIT, snapshot_sum_path),
            "snapshot SHA256SUMS",
        )
        for expected, snapshot_path in snapshot_entries:
            repo_path = snapshot_path_to_repo(snapshot_path)
            actual = sha256(git_blob(SNAPSHOT_COMMIT, repo_path))
            if actual != expected:
                errors.append("snapshot checksum mismatch: {}".format(snapshot_path))

        manifest_path = SNAPSHOT_DOCS + "/file_manifest.txt"
        payload_entries = parse_payload_manifest(
            git_blob(SNAPSHOT_COMMIT, manifest_path)
        )
        for expected, expected_size, path in payload_entries:
            data = git_blob(SNAPSHOT_COMMIT, path)
            if len(data) != expected_size or sha256(data) != expected:
                errors.append("snapshot payload mismatch: {}".format(path))
    except (RuntimeError, UnicodeDecodeError, ValueError) as exc:
        errors.append("snapshot history verification failed: {}".format(exc))

    for path in IMMUTABLE_SNAPSHOT_RECORDS:
        current_path = ROOT / path
        if not current_path.is_file():
            errors.append("immutable snapshot record is missing: {}".format(path))
            continue
        try:
            original = git_blob(SNAPSHOT_COMMIT, path)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        try:
            current_blob = git_blob("HEAD", path)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if current_blob != original:
            errors.append("immutable snapshot record changed: {}".format(path))


def verify_paths(paths: Sequence[Path], errors: List[str]) -> Dict[Path, bytes]:
    modes = tracked_modes()
    casefold_paths: Dict[str, str] = {}
    text_data: Dict[Path, bytes] = {}

    for path in paths:
        relative = path.as_posix()
        folded = relative.casefold()
        if folded in casefold_paths and casefold_paths[folded] != relative:
            errors.append(
                "case-colliding tracked paths: {} and {}".format(
                    casefold_paths[folded], relative
                )
            )
        casefold_paths[folded] = relative

        absolute = ROOT / path
        if not absolute.is_file():
            errors.append("tracked file is missing from the checkout: {}".format(relative))
            continue
        data = absolute.read_bytes()
        append_path_policy_errors(
            path,
            len(data),
            modes.get(relative, ""),
            "working tree",
            errors,
        )
        if append_blob_policy_errors(path, data, "working tree", errors):
            text_data[path] = data

    return text_data


def verify_commit_history(errors: List[str]) -> None:
    commits = [
        item.decode("ascii")
        for item in git_bytes(
            "rev-list",
            "--reverse",
            "{}..HEAD".format(SNAPSHOT_COMMIT),
        ).splitlines()
        if item
    ]
    for commit in commits:
        context = "commit {}".format(commit[:12])
        message = git_bytes("show", "-s", "--format=%B", commit)
        append_blob_policy_errors(
            Path("COMMIT_MESSAGE"),
            message,
            context,
            errors,
        )
        output = git_bytes("ls-tree", "-r", "-l", "-z", commit)
        casefold_paths: Dict[str, str] = {}
        for item in output.split(b"\0"):
            if not item:
                continue
            metadata, raw_path = item.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 4:
                errors.append("{}: invalid git tree entry".format(context))
                continue
            mode = fields[0].decode("ascii")
            object_type = fields[1].decode("ascii")
            object_id = fields[2].decode("ascii")
            raw_size = fields[3].decode("ascii")
            path = Path(raw_path.decode("utf-8"))
            relative = path.as_posix()

            folded = relative.casefold()
            if folded in casefold_paths and casefold_paths[folded] != relative:
                errors.append(
                    "{}: case-colliding paths {} and {}".format(
                        context,
                        casefold_paths[folded],
                        relative,
                    )
                )
            casefold_paths[folded] = relative

            size = int(raw_size) if raw_size.isdigit() else 0
            append_path_policy_errors(path, size, mode, context, errors)
            if object_type != "blob":
                continue
            data = git_bytes("cat-file", "blob", object_id)
            append_blob_policy_errors(path, data, context, errors)
            if relative.startswith(".github/workflows/") and path.suffix.casefold() in {
                ".yml",
                ".yaml",
            }:
                expected_hash = APPROVED_WORKFLOW_HASHES.get(relative)
                if expected_hash is None:
                    errors.append("{}: historical workflow is not approved: {}".format(context, relative))
                elif sha256(data) != expected_hash:
                    errors.append("{}: historical workflow content is not approved: {}".format(context, relative))


def verify_environment_template(errors: List[str]) -> None:
    expected = b"PATROL_ELDER_PROFILE_ID=\nPATROL_ELDER_CODE=\n"
    path = ROOT / ".env.example"
    if not path.is_file() or path.read_bytes().replace(b"\r\n", b"\n") != expected:
        errors.append(".env.example must contain exactly two empty patrol identity values")


def verify_sanitized_runner(errors: List[str]) -> None:
    path = ROOT / "patrol_ai" / "patrol_ai_runner.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        errors.append("cannot parse sanitized patrol runner: {}".format(exc))
        return

    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "mock_recognize_face":
            target = node
            break
    if target is None:
        errors.append("mock_recognize_face is missing")
        return

    os_imports = [
        alias
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os" and alias.asname is None
    ]
    if len(os_imports) != 1:
        errors.append("patrol runner must bind os through exactly one plain import")
    append_module_os_integrity_errors(tree, errors)
    if any(
        isinstance(node, ast.Name)
        and node.id == "os"
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(target)
    ):
        errors.append("mock_recognize_face must not shadow the os module")

    returns = [node for node in ast.walk(target) if isinstance(node, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0].value, ast.Dict):
        errors.append("mock_recognize_face must have one dictionary return value")
        return

    field_values: Dict[str, ast.AST] = {}
    returned = returns[0].value
    for key, value in zip(returned.keys, returned.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if key.value in field_values:
            errors.append("duplicate mock identity key: {}".format(key.value))
        field_values[key.value] = value

    expected = dict(
        (
            ("elderProfileId", "PATROL_ELDER_PROFILE_ID"),
            ("elderCode", "PATROL_ELDER_CODE"),
        )
    )
    for field, environment_name in expected.items():
        value = field_values.get(field)
        valid = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "getenv"
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "os"
            and len(value.args) == 2
            and not value.keywords
            and isinstance(value.args[0], ast.Constant)
            and value.args[0].value == environment_name
            and isinstance(value.args[1], ast.Constant)
            and value.args[1].value == ""
        )
        if not valid:
            errors.append("{} must read {} with an empty default".format(field, environment_name))


class ModuleOsIntegrityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.os_rebound = False
        self.getenv_rebound = False
        self.getenv_setattr = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", 1)[0]
            if bound_name == "os" and not (alias.name == "os" and alias.asname is None):
                self.os_rebound = True

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if (alias.asname or alias.name) == "os":
                self.os_rebound = True

    def visit_function_definition(self, node: ast.AST) -> None:
        if getattr(node, "name", None) == "os":
            self.os_rebound = True
        for decorator in getattr(node, "decorator_list", ()):
            self.visit(decorator)
        self.visit(node.args)
        returns = getattr(node, "returns", None)
        if returns is not None:
            self.visit(returns)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        for statement in node.body:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.visit_function_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_function_definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == "os":
            self.os_rebound = True
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        for statement in node.body:
            self.visit(statement)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.visit(node.body)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "os" and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.os_rebound = True

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr == "getenv"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            self.getenv_rebound = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "os"
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "getenv"
        ):
            self.getenv_setattr = True
        self.generic_visit(node)


def append_module_os_integrity_errors(tree: ast.Module, errors: List[str]) -> None:
    visitor = ModuleOsIntegrityVisitor()
    visitor.visit(tree)
    if visitor.os_rebound:
        errors.append("patrol runner must not reassign the os module at module scope")
    if visitor.getenv_rebound:
        errors.append("patrol runner must not reassign os.getenv at module scope")
    if visitor.getenv_setattr:
        errors.append("patrol runner must not replace os.getenv through setattr")


def verify_gitignore(errors: List[str]) -> None:
    path = ROOT / ".gitignore"
    if not path.is_file():
        errors.append(".gitignore is missing")
        return
    rules = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = sorted(REQUIRED_GITIGNORE_RULES - rules)
    if missing:
        errors.append(".gitignore is missing required rules: {}".format(", ".join(missing)))

    for sample in IGNORE_SAMPLES:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "--", sample],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            errors.append(".gitignore does not reject sample path {}".format(sample))

    example = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", ".env.example"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if example.returncode == 0:
        errors.append(".env.example must not be ignored")


def verify_workflows(paths: Sequence[Path], text_data: Dict[Path, bytes], errors: List[str]) -> None:
    workflows = [
        path
        for path in paths
        if path.as_posix().startswith(".github/workflows/")
        and path.suffix.casefold() in {".yml", ".yaml"}
    ]
    if not workflows:
        errors.append("no GitHub Actions workflow is tracked")
        return
    workflow_names = {path.as_posix() for path in workflows}
    approved_names = set(APPROVED_WORKFLOW_HASHES)
    if workflow_names != approved_names:
        errors.append(
            "workflow set differs from the approved set: {}".format(
                ", ".join(sorted(workflow_names))
            )
        )

    forbidden_patterns = (
        (re.compile(r"(?m)^\s*pull_request_target\s*:"), "pull_request_target"),
        (re.compile(r"(?m)^\s*workflow_run\s*:"), "workflow_run"),
        (re.compile(r"(?m)^\s*schedule\s*:"), "scheduled execution"),
        (re.compile(r"(?i)self-hosted"), "self-hosted runner"),
        (re.compile(r"\$\{\{\s*secrets\."), "GitHub secret access"),
        (re.compile(r"(?m)^\s*id-token\s*:\s*write\s*$"), "OIDC write permission"),
        (
            re.compile(
                r"(?m)^\s*(?:actions|checks|contents|deployments|packages|"
                r"pull-requests|statuses)\s*:\s*write\s*$"
            ),
            "write permission",
        ),
        (re.compile(r"(?m)^\s*paths(?:-ignore)?\s*:"), "path-filtered safety CI"),
        (re.compile(r"(?m)^\s*environment\s*:"), "deployment environment"),
        (re.compile(r"(?i)\b(?:ssh|scp|rsync)\b"), "remote shell command"),
        (re.compile(r"(?i)\bgit\s+push\b"), "Git push command"),
        (re.compile(r"(?i)\bgh\s+release\b"), "GitHub release command"),
        (re.compile(r"(?i)upload-artifact|download-artifact"), "artifact transfer"),
    )

    for path in workflows:
        data = text_data[path]
        text = data.decode("utf-8")
        expected_hash = APPROVED_WORKFLOW_HASHES.get(path.as_posix())
        indexed_data = git_index_blob(path.as_posix())
        if expected_hash is None or sha256(indexed_data) != expected_hash:
            errors.append("workflow content is not approved: {}".format(path.as_posix()))
        for pattern, description in forbidden_patterns:
            if pattern.search(text):
                errors.append("{} is forbidden in {}".format(description, path.as_posix()))
        if not re.search(r"(?m)^permissions:\s*\n\s+contents:\s*read\s*$", text):
            errors.append("workflow must declare contents: read in {}".format(path.as_posix()))
        if "persist-credentials: false" not in text:
            errors.append("checkout credentials must not persist in {}".format(path.as_posix()))
        if "timeout-minutes:" not in text:
            errors.append("each workflow must define timeouts in {}".format(path.as_posix()))
        for line_number, line in enumerate(text.splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s]+)", line)
            if not match:
                continue
            action = match.group(1)
            if action.startswith("./"):
                errors.append(
                    "local actions are not approved in {}:{}".format(
                        path.as_posix(), line_number
                    )
                )
                continue
            if action not in APPROVED_ACTIONS:
                errors.append(
                    "action is outside the approved allowlist in {}:{}: {}".format(
                        path.as_posix(), line_number, action
                    )
                )


def verify_codeowners(errors: List[str]) -> None:
    path = ROOT / ".github" / "CODEOWNERS"
    if not path.is_file():
        errors.append(".github/CODEOWNERS is missing")
        return
    text = path.read_text(encoding="utf-8")
    required_fragments = (
        "/.github/workflows/ @DoyeonKing",
        "/.gitignore @DoyeonKing",
        "/SHA256SUMS @DoyeonKing",
        "/docs/snapshot-20260714/ @DoyeonKing",
        "/tools/ci/ @DoyeonKing",
    )
    for fragment in required_fragments:
        if fragment not in text:
            errors.append("CODEOWNERS is missing {}".format(fragment))


def verify_python_syntax(paths: Iterable[Path], errors: List[str]) -> None:
    for path in paths:
        if path.suffix.casefold() != ".py":
            continue
        try:
            compile(
                (ROOT / path).read_bytes(),
                path.as_posix(),
                "exec",
                flags=ast.PyCF_ONLY_AST,
                dont_inherit=True,
            )
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append("Python syntax check failed for {}: {}".format(path.as_posix(), exc))


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--syntax-only", action="store_true")
    args = parser.parse_args(list(argv))

    errors: List[str] = []
    paths = tracked_paths()
    verify_python_syntax(paths, errors)

    if not args.syntax_only:
        text_data = verify_paths(paths, errors)
        verify_environment_template(errors)
        verify_sanitized_runner(errors)
        verify_gitignore(errors)
        verify_workflows(paths, text_data, errors)
        verify_codeowners(errors)
        verify_snapshot_history(errors)
        verify_commit_history(errors)

    if errors:
        for error in errors:
            print("ERROR: {}".format(error), file=sys.stderr)
        return 1

    mode = "syntax" if args.syntax_only else "repository policy"
    print("{} verification passed for {} tracked files".format(mode, len(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

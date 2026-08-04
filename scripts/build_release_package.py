"""Build a deterministic, secret-safe submission archive from Git-tracked files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

PACKAGE_ROOT = "security-superagent"
FORBIDDEN_TRACKED_PATHS = {
    ".env",
    ".coverage",
}
FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "outputs",
    "runtime",
    "test-tmp",
    "artifact-work",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".onnx",
    ".pyc",
    ".sqlite3",
}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _tracked_files(root: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [root / item.decode("utf-8") for item in output.split(b"\0") if item]


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_violation(relative_path: str) -> str | None:
    normalized = PurePosixPath(relative_path)
    if relative_path in FORBIDDEN_TRACKED_PATHS:
        return "forbidden tracked file"
    if any(part in FORBIDDEN_PARTS for part in normalized.parts):
        return "forbidden runtime or cache directory"
    if normalized.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "forbidden runtime/model extension"
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_release(root: Path, output_dir: Path, *, docker_memory_gb: int) -> tuple[Path, Path]:
    commit = _git(root, "rev-parse", "HEAD")
    short_commit = commit[:12]
    commit_timestamp = int(_git(root, "show", "-s", "--format=%ct", "HEAD"))
    commit_time = datetime.fromtimestamp(commit_timestamp, tz=UTC)
    zip_time = (
        commit_time.year,
        commit_time.month,
        commit_time.day,
        commit_time.hour,
        commit_time.minute,
        commit_time.second,
    )
    tracked = _tracked_files(root)
    file_records: list[dict[str, object]] = []
    payloads: list[tuple[str, bytes]] = []
    violations: list[str] = []
    for path in tracked:
        relative_path = _relative(root, path)
        violation = _path_violation(relative_path)
        if violation:
            violations.append(f"{relative_path}: {violation}")
            continue
        data = path.read_bytes()
        payloads.append((relative_path, data))
        file_records.append(
            {
                "path": relative_path,
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        )
    if violations:
        raise RuntimeError("release package refused:\n" + "\n".join(violations))

    evaluation = json.loads((root / "evaluations/gold/triage-rag-30.json").read_text("utf-8"))
    manifest = {
        "package_schema": "security-superagent-release/v1",
        "commit": commit,
        "created_from_commit_time": commit_time.isoformat(),
        "tracked_file_count": len(file_records),
        "docker_memory_gb": docker_memory_gb,
        "video_deliverable_required": False,
        "gold_evaluation_review_status": evaluation["review_status"],
        "platform_adapter_status": "awaiting_vendor_materials",
        "included": [
            "source code and tests",
            "Docker deployment",
            "documentation and PPT",
            "gold and adversarial evaluation sets",
            "MCP gateway and Skill v1",
        ],
        "excluded": [
            ".env and all credentials",
            "SQLite/runtime data",
            "knowledge indexes and ONNX models",
            "cache and test artifacts",
            "recorded demo videos (not required by user)",
        ],
        "files": file_records,
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    checksum_lines = [f"{record['sha256']}  {record['path']}" for record in file_records]
    checksum_lines.append(f"{_sha256(manifest_bytes)}  RELEASE-MANIFEST.json")
    checksums_bytes = ("\n".join(checksum_lines) + "\n").encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"security-superagent-release-candidate-{short_commit}.zip"
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zip_file:
        for relative_path, data in payloads:
            info = zipfile.ZipInfo(f"{PACKAGE_ROOT}/{relative_path}", date_time=zip_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zip_file.writestr(info, data)
        for name, data in (
            ("RELEASE-MANIFEST.json", manifest_bytes),
            ("CHECKSUMS.sha256", checksums_bytes),
        ):
            info = zipfile.ZipInfo(f"{PACKAGE_ROOT}/{name}", date_time=zip_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zip_file.writestr(info, data)
    digest_path = archive.with_suffix(".zip.sha256")
    digest_path.write_text(f"{_sha256(archive.read_bytes())}  {archive.name}\n", encoding="utf-8")
    return archive, digest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--docker-memory-gb", type=int, default=4)
    args = parser.parse_args()
    archive, digest = build_release(
        args.root.resolve(),
        args.output_dir.resolve(),
        docker_memory_gb=args.docker_memory_gb,
    )
    print(
        json.dumps(
            {
                "archive": str(archive),
                "sha256_file": str(digest),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

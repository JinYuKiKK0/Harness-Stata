from __future__ import annotations

from pathlib import Path


def iter_artifact_matches(working_dir: Path, patterns: tuple[str, ...]) -> list[Path]:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in working_dir.glob(pattern) if path.is_file())
    return sorted(matches)


def snapshot_artifacts(working_dir: Path, patterns: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in iter_artifact_matches(working_dir, patterns):
        stat = path.stat()
        snapshot[str(path.resolve())] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def collect_artifacts(
    working_dir: Path,
    patterns: tuple[str, ...],
    before_snapshot: dict[str, tuple[int, int]],
) -> list[str]:
    artifacts: list[str] = []
    seen: set[str] = set()
    for path in iter_artifact_matches(working_dir, patterns):
        resolved = str(path.resolve())
        stat = path.stat()
        marker = (stat.st_mtime_ns, stat.st_size)
        if before_snapshot.get(resolved) != marker and resolved not in seen:
            artifacts.append(resolved)
            seen.add(resolved)
    artifacts.sort()
    return artifacts

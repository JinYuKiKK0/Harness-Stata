from __future__ import annotations

import os
from pathlib import Path
import sys

from ..contract import Edition


_EDITION_PREFIX = {
    "mp": "statamp",
    "se": "statase",
    "be": "statabe",
}
_HEADLESS_HINTS = ("console", "batch", "automation", "headless")


def resolve_stata_executable(
    stata_executable: str | None,
    edition: Edition,
) -> Path | None:
    if not stata_executable:
        return None

    return _resolve_candidate(Path(stata_executable).expanduser(), edition)


def find_preferred_executable(directory: Path, edition: Edition) -> Path | None:
    if not directory.exists() or not directory.is_dir():
        return None

    prefix = _EDITION_PREFIX[edition]
    candidates = [path for path in directory.glob("*.exe") if path.stem.lower().startswith(prefix)]
    if not candidates:
        return None

    def score(candidate: Path) -> tuple[int, int, int, str]:
        name = candidate.name.lower()
        headless_rank = 0 if any(hint in name for hint in _HEADLESS_HINTS) else 1
        sixty_four_rank = 0 if "64" in name else 1
        gui_rank = 1 if name.startswith(prefix) else 2
        return (headless_rank, sixty_four_rank, gui_rank, name)

    candidates.sort(key=score)
    return candidates[0].resolve()


def build_stata_command(executable: Path, wrapper_do_path: Path) -> list[str]:
    if os.name == "nt":
        return [str(executable), "/q", "/i", "/e", "do", str(wrapper_do_path)]
    compat_command = _build_posix_compat_command(executable, wrapper_do_path)
    if compat_command is not None:
        return compat_command
    return [str(executable), "-b", "do", str(wrapper_do_path)]


def _resolve_candidate(path: Path, edition: Edition) -> Path | None:
    if path.exists() and path.is_file():
        preferred = find_preferred_executable(path.parent, edition)
        if preferred is not None:
            return preferred
        return path.resolve()

    if path.exists() and path.is_dir():
        return find_preferred_executable(path, edition)

    return None


def _build_posix_compat_command(
    executable: Path,
    wrapper_do_path: Path,
) -> list[str] | None:
    if executable.suffix.lower() not in {".cmd", ".bat"}:
        return None

    # Test fixtures use a Windows batch wrapper that forwards to a sibling Python script.
    # On POSIX that wrapper is not directly executable, so invoke the sibling helper instead.
    python_sibling = executable.with_suffix(".py")
    if python_sibling.exists():
        return [sys.executable, str(python_sibling), str(wrapper_do_path)]
    return None

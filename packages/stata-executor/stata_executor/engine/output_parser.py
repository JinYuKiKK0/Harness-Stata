from __future__ import annotations

import re

from ..contract import ErrorKind

_COMMAND_ECHO_PATTERN = re.compile(r"^\.\s*$|^\.\s+\S")
_NUMBERED_LINE_PATTERN = re.compile(r"^\s*\d+\.\s")
_CONTINUATION_PATTERN = re.compile(r"^>\s")
_LOG_INFO_PATTERN = re.compile(
    r"^\s*(name:|log:|log type:|opened on:|closed on:|Log file saved to:)",
    re.IGNORECASE,
)


def parse_exit_code(text: str, fallback: int) -> int:
    match = re.findall(r"__AGENT_RC__\s*=\s*(\d+)", text)
    if match:
        return int(match[-1])
    generic = re.findall(r"r\((\d+)\)", text)
    if generic:
        return int(generic[-1])
    return fallback


def classify_execution_failure(text: str, exit_code: int) -> ErrorKind:
    low = text.lower()
    if exit_code in {198, 199}:
        return "stata_parse_or_command_error"
    if "invalid syntax" in low or "unrecognized" in low:
        return "stata_parse_or_command_error"
    return "stata_runtime_error"


def build_execution_summary(text: str, exit_code: int) -> str:
    if exit_code == 0:
        return "Stata do-file completed successfully."

    error_signature = extract_error_signature(text, exit_code)
    if error_signature:
        return f"Stata execution failed with exit_code={exit_code}: {error_signature}"
    return f"Stata execution failed with exit_code={exit_code}."


def build_bootstrap_summary(text: str) -> str:
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    if stripped:
        return f"Stata subprocess bootstrap failed: {stripped[-1]}"
    return "Stata subprocess bootstrap failed before any execution log was created."


def render_result_text(text: str) -> str:
    if not text:
        return ""

    filtered: list[str] = []
    previous_blank = False
    for line in strip_agent_rc_trailer(text.splitlines()):
        if _COMMAND_ECHO_PATTERN.match(line):
            continue
        if _NUMBERED_LINE_PATTERN.match(line):
            continue
        if _CONTINUATION_PATTERN.match(line):
            continue
        if _LOG_INFO_PATTERN.match(line):
            continue

        is_blank = not line.strip()
        if is_blank:
            if previous_blank:
                continue
            filtered.append("")
            previous_blank = True
            continue

        filtered.append(line.rstrip())
        previous_blank = False

    while filtered and not filtered[-1].strip():
        filtered.pop()
    blocks = extract_empirical_result_blocks(filtered)
    if blocks:
        return "\n\n".join(blocks)
    return "\n".join(filtered)


def extract_empirical_result_blocks(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if re.search(r"\bregression\b", line, re.IGNORECASE) and "Number of obs" in line:
            block_lines = [line.rstrip()]
            index += 1
            while index < len(lines):
                current = lines[index].rstrip()
                block_lines.append(current)
                if lines[index].startswith("F test that all u_i=0:"):
                    break
                index += 1
            block = "\n".join(block_lines).strip()
            if block:
                blocks.append(block)
            index += 1
            continue

        if re.match(r"^\s*Variable\s+\|\s+Obs", line):
            block_lines = [line.rstrip()]
            index += 1
            while index < len(lines):
                current = lines[index]
                current_stripped = current.strip()
                if not current_stripped:
                    break
                if "|" in current or re.match(r"^-+[+-]-+$", current_stripped) or re.match(r"^-+$", current_stripped):
                    block_lines.append(current.rstrip())
                    index += 1
                    continue
                break
            block = "\n".join(block_lines).strip()
            if block:
                blocks.append(block)
            continue

        index += 1

    return blocks


def extract_diagnostics(text: str, exit_code: int) -> tuple[str, str | None, str | None]:
    if not text:
        return "", None, None
    if exit_code == 0:
        return "", None, None

    lines = text.splitlines()
    command_start, failed_command = extract_last_command_block(lines)
    error_index, error_signature = extract_error_signature_with_index(lines, exit_code)

    if command_start is not None and error_index is not None and command_start <= error_index:
        excerpt_start = command_start
    elif error_index is not None:
        excerpt_start = error_index
    elif command_start is not None:
        excerpt_start = command_start
    else:
        excerpt_start = 0

    excerpt_lines = strip_agent_rc_trailer(lines[excerpt_start:])
    excerpt = "\n".join(excerpt_lines).strip()
    return excerpt, error_signature, failed_command


def extract_last_command_block(lines: list[str]) -> tuple[int | None, str | None]:
    block_start: int | None = None
    block_lines: list[str] = []
    blocks: list[tuple[int, str]] = []

    for index, raw_line in enumerate(lines):
        if raw_line.startswith(". "):
            if block_start is not None and block_lines:
                blocks.append((block_start, "\n".join(block_lines).strip()))
            block_start = index
            block_lines = [raw_line[2:].rstrip()]
            continue

        if raw_line.startswith("> ") and block_start is not None:
            block_lines.append(raw_line[2:].rstrip())

    if block_start is not None and block_lines:
        blocks.append((block_start, "\n".join(block_lines).strip()))

    if not blocks:
        return None, None
    return blocks[-1]


def extract_error_signature_with_index(lines: list[str], exit_code: int) -> tuple[int | None, str | None]:
    if exit_code == 0:
        return None, None

    final_rc_index: int | None = None
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].strip()
        if re.fullmatch(r"r\(\d+\);?", stripped):
            final_rc_index = index
            break

    search_end = final_rc_index if final_rc_index is not None else len(lines)
    for index in range(search_end - 1, -1, -1):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if stripped.startswith("__AGENT_RC__") or stripped.startswith("r("):
            continue
        if lines[index].startswith(". ") or lines[index].startswith("> "):
            continue
        return index, stripped
    return None, None


def extract_error_signature(text: str, exit_code: int) -> str | None:
    _, signature = extract_error_signature_with_index(text.splitlines(), exit_code)
    return signature


def extract_last_meaningful_line(text: str) -> str | None:
    for raw_line in reversed(text.splitlines()):
        stripped = raw_line.strip()
        if stripped:
            return stripped
    return None


def strip_agent_rc_trailer(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and trimmed[-1].strip().startswith("__AGENT_RC__"):
        trimmed.pop()
    return trimmed


def strip_agent_rc_trailer_text(text: str) -> str:
    return "\n".join(strip_agent_rc_trailer(text.splitlines())).strip()

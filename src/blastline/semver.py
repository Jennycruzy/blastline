"""Conservative version and range evaluation for registry evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass


class UnsupportedRange(ValueError):
    """A range is not understood well enough to make a security claim."""


@dataclass(frozen=True, slots=True, order=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...] = ()


_VERSION = re.compile(r"^[v=\s]*(\d+)(?:\.(\d+|x|X|\*))?(?:\.(\d+|x|X|\*))?(?:-([0-9A-Za-z.-]+))?")


def parse_version(value: str) -> Version:
    candidate = value.strip()
    match = _VERSION.match(candidate)
    if match is None or match.group(0).strip() != candidate:
        raise UnsupportedRange(f"unsupported version: {value!r}")
    minor = match.group(2)
    patch = match.group(3)
    if minor in (None, "x", "X", "*"):
        minor_number = 0
    else:
        minor_number = int(minor)
    if patch in (None, "x", "X", "*"):
        patch_number = 0
    else:
        patch_number = int(patch)
    prerelease_value = match.group(4)
    prerelease: tuple[str | int, ...] = ()
    if prerelease_value is not None:
        parts: list[str | int] = []
        for part in prerelease_value.split("."):
            parts.append(int(part) if part.isdigit() else part)
        prerelease = tuple(parts)
    return Version(int(match.group(1)), minor_number, patch_number, prerelease)


def compare(left: Version, right: Version) -> int:
    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    if not left.prerelease and not right.prerelease:
        return 0
    if not left.prerelease:
        return 1
    if not right.prerelease:
        return -1
    for left_part, right_part in zip(left.prerelease, right.prerelease):
        if left_part == right_part:
            continue
        if isinstance(left_part, int) and isinstance(right_part, str):
            return -1
        if isinstance(left_part, str) and isinstance(right_part, int):
            return 1
        return 1 if left_part > right_part else -1
    if len(left.prerelease) == len(right.prerelease):
        return 0
    return 1 if len(left.prerelease) > len(right.prerelease) else -1


def _upper_for_caret(version: Version) -> Version:
    if version.major > 0:
        return Version(version.major + 1, 0, 0)
    if version.minor > 0:
        return Version(0, version.minor + 1, 0)
    return Version(0, 0, version.patch + 1)


def _upper_for_tilde(version: Version) -> Version:
    return Version(version.major, version.minor + 1, 0)


def _comparison(version: Version, operator: str, target: Version) -> bool:
    result = compare(version, target)
    if operator in ("", "="):
        return result == 0
    if operator == ">":
        return result > 0
    if operator == ">=":
        return result >= 0
    if operator == "<":
        return result < 0
    if operator == "<=":
        return result <= 0
    raise UnsupportedRange(f"unsupported comparison operator: {operator}")


def _matches_and(version: Version, expression: str, ecosystem: str) -> bool:
    candidate = expression.strip()
    if candidate in ("", "*", "x", "X"):
        return True
    if ecosystem == "PyPI" and candidate.startswith("~="):
        lower = parse_version(candidate[2:].strip())
        return compare(version, lower) >= 0 and compare(version, Version(lower.major + 1, 0, 0)) < 0
    tokens = [token for token in re.split(r"\s*,\s*|\s+", candidate) if token]
    for token in tokens:
        if token in ("*", "x", "X"):
            continue
        if token.startswith("^"):
            lower = parse_version(token[1:])
            if compare(version, lower) < 0 or compare(version, _upper_for_caret(lower)) >= 0:
                return False
            continue
        if token.startswith("~"):
            lower = parse_version(token[1:])
            if compare(version, lower) < 0 or compare(version, _upper_for_tilde(lower)) >= 0:
                return False
            continue
        match = re.match(r"^(>=|<=|>|<|=)?(.+)$", token)
        if match is None:
            raise UnsupportedRange(f"unsupported range token: {token}")
        operator = match.group(1) if match.group(1) is not None else ""
        target_text = match.group(2)
        if target_text.endswith((".x", ".X", ".*")) or target_text.count(".") < 2:
            parts = target_text.lstrip("v=").split(".")
            if not parts or not parts[0].isdigit():
                raise UnsupportedRange(f"unsupported wildcard range: {token}")
            major = int(parts[0])
            if len(parts) == 1 or parts[1] in ("x", "X", "*"):
                lower = Version(major, 0, 0)
                upper = Version(major + 1, 0, 0)
            else:
                minor = int(parts[1])
                lower = Version(major, minor, 0)
                upper = Version(major, minor + 1, 0)
            if operator not in ("", "="):
                raise UnsupportedRange(f"operator with wildcard range is unsupported: {token}")
            if compare(version, lower) < 0 or compare(version, upper) >= 0:
                return False
            continue
        if not _comparison(version, operator, parse_version(target_text)):
            return False
    return True


def satisfies(version_text: str, expression: str, ecosystem: str) -> bool:
    version = parse_version(version_text)
    alternatives = [part.strip() for part in expression.split("||")]
    if not alternatives:
        raise UnsupportedRange("empty range")
    return any(_matches_and(version, part, ecosystem) for part in alternatives)

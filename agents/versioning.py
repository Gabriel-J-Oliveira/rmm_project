import re
from functools import cmp_to_key


SEMVER_PATTERN = re.compile(
    r'(?P<version>\d+(?:\.\d+){1,3}(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?'
)


def normalize_agent_version(value):
    raw = str(value or '').strip()
    if not raw:
        return ''
    match = SEMVER_PATTERN.search(raw)
    if not match:
        return ''
    version = match.group('version')
    return version[:50]


def parse_semver(value):
    raw = normalize_agent_version(value)
    if not raw:
        return None
    version_core = raw
    if '-' in version_core:
        version_core, prerelease = version_core.split('-', 1)
    else:
        prerelease = ''
    parts = version_core.split('.')
    if not parts or any(not part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    while len(numbers) < 4:
        numbers.append(0)
    prerelease_parts = tuple(prerelease.split('.')) if prerelease else ()
    return tuple(numbers[:4]), prerelease_parts


def compare_prerelease(left, right):
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        comparison = compare_prerelease_identifier(left_part, right_part)
        if comparison != 0:
            return comparison
    if len(left) < len(right):
        return -1
    if len(left) > len(right):
        return 1
    return 0


def prerelease_identifier_tokens(value):
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.findall(r'\d+|[A-Za-z]+|[^A-Za-z0-9]+', str(value or ''))
    )


def compare_prerelease_identifier(left, right):
    left_numeric = left.isdigit()
    right_numeric = right.isdigit()
    if left_numeric and right_numeric:
        left_tokens = (int(left),)
        right_tokens = (int(right),)
    elif left_numeric:
        return -1
    elif right_numeric:
        return 1
    else:
        left_tokens = prerelease_identifier_tokens(left)
        right_tokens = prerelease_identifier_tokens(right)

    for left_token, right_token in zip(left_tokens, right_tokens):
        if isinstance(left_token, int) and isinstance(right_token, int):
            if left_token < right_token:
                return -1
            if left_token > right_token:
                return 1
            continue
        if isinstance(left_token, int):
            return -1
        if isinstance(right_token, int):
            return 1
        if left_token < right_token:
            return -1
        if left_token > right_token:
            return 1
    if len(left_tokens) < len(right_tokens):
        return -1
    if len(left_tokens) > len(right_tokens):
        return 1
    return 0


def compare_versions(current, recommended):
    current_parsed = parse_semver(current)
    recommended_parsed = parse_semver(recommended)
    if current_parsed is None or recommended_parsed is None:
        return None
    current_numbers, current_prerelease = current_parsed
    recommended_numbers, recommended_prerelease = recommended_parsed
    if current_numbers < recommended_numbers:
        return -1
    if current_numbers > recommended_numbers:
        return 1
    return compare_prerelease(current_prerelease, recommended_prerelease)


def sort_versions(values, *, reverse=False):
    return sorted(
        values,
        key=cmp_to_key(lambda left, right: compare_versions(left, right) if compare_versions(left, right) is not None else 0),
        reverse=reverse,
    )


def sort_releases_by_version(releases, *, reverse=True):
    return sorted(
        releases,
        key=cmp_to_key(
            lambda left, right: compare_versions(getattr(left, 'version', ''), getattr(right, 'version', ''))
            if compare_versions(getattr(left, 'version', ''), getattr(right, 'version', '')) is not None
            else 0
        ),
        reverse=reverse,
    )


def agent_version_state(current, recommended):
    if not current:
        return 'unknown'
    comparison = compare_versions(current, recommended)
    if comparison is None:
        return 'outdated'
    if comparison < 0:
        return 'outdated'
    return 'current'

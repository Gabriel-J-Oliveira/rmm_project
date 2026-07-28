import re


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
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            left_value = int(left_part)
            right_value = int(right_part)
        elif left_numeric:
            return -1
        elif right_numeric:
            return 1
        else:
            left_value = left_part
            right_value = right_part
        if left_value < right_value:
            return -1
        if left_value > right_value:
            return 1
    if len(left) < len(right):
        return -1
    if len(left) > len(right):
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


def agent_version_state(current, recommended):
    if not current:
        return 'unknown'
    comparison = compare_versions(current, recommended)
    if comparison is None:
        return 'outdated'
    if comparison < 0:
        return 'outdated'
    return 'current'

def parse_semver(value):
    parts = str(value or '').strip().split('.')
    if not parts or any(not part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])


def compare_versions(current, recommended):
    current_parsed = parse_semver(current)
    recommended_parsed = parse_semver(recommended)
    if current_parsed is None or recommended_parsed is None:
        return None
    if current_parsed < recommended_parsed:
        return -1
    if current_parsed > recommended_parsed:
        return 1
    return 0


def agent_version_state(current, recommended):
    if not current:
        return 'unknown'
    comparison = compare_versions(current, recommended)
    if comparison is None:
        return 'outdated'
    if comparison < 0:
        return 'outdated'
    return 'current'

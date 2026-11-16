"""Fail-closed UUID allowlist for the Windows service collection feature."""
import uuid


def parse_endpoint_uuid_allowlist(values):
    """Parse configured endpoint IDs into canonical UUID strings.

    Empty configuration is a valid deny-all policy. Any malformed entry is an
    error so a typo cannot silently broaden or partially change the policy.
    """
    if values is None or values == "":
        return frozenset()
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("endpoint allowlist must be a sequence of UUIDs")

    parsed = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("endpoint allowlist contains an invalid UUID")
        try:
            parsed.add(str(uuid.UUID(value.strip())))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("endpoint allowlist contains an invalid UUID") from None
    return frozenset(parsed)


def service_collection_endpoint_allowed(endpoint_id, configured_ids):
    """Return (allowed, reason); runtime-invalid overrides always deny."""
    try:
        allowed_ids = parse_endpoint_uuid_allowlist(configured_ids)
        canonical_endpoint_id = str(uuid.UUID(str(endpoint_id)))
    except (ValueError, AttributeError, TypeError):
        return False, "allowlist_invalid"
    if not allowed_ids or canonical_endpoint_id not in allowed_ids:
        return False, "endpoint_not_allowed"
    return True, ""

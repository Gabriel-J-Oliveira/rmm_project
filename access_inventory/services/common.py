class ImportStats:
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.ignored = 0

    def bump(self, created):
        if created:
            self.created += 1
        else:
            self.updated += 1

    def as_dict(self):
        return {
            'created': self.created,
            'updated': self.updated,
            'ignored': self.ignored,
        }


class ImportResult:
    def __init__(self, stats, errors=None):
        self.stats = stats
        self.errors = errors or []

    @property
    def created(self):
        return sum(item.created for item in self.stats.values())

    @property
    def updated(self):
        return sum(item.updated for item in self.stats.values())

    @property
    def ignored(self):
        return sum(item.ignored for item in self.stats.values())

    @property
    def errors_count(self):
        return len(self.errors)

    def as_dict(self):
        return {
            'created': self.created,
            'updated': self.updated,
            'ignored': self.ignored,
            'errors': self.errors_count,
            'details': {
                label: stats.as_dict()
                for label, stats in self.stats.items()
            },
        }


def value(row, *keys, default=''):
    for key in keys:
        if key in row and row[key] not in (None, ''):
            return row[key]
    return default


def as_list(data, *keys):
    if isinstance(data, list):
        return data
    for key in keys:
        items = data.get(key)
        if isinstance(items, list):
            return items
    return []


def as_bool(raw, default=False):
    if raw in (None, ''):
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'sim', 'enabled'}


def lower_choice(raw, allowed, default):
    item = str(raw or '').strip().lower()
    return item if item in allowed else default

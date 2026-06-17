from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from django.db.models import Q
from django.db import transaction

from access_inventory.models import AccessReviewFolder, AccessReviewPlan, Folder


@dataclass
class FolderSeedItem:
    source_folder: Folder
    proposed_path: str
    name: str
    area_name: str
    parent_path: str
    level: int
    sort_order: int
    notes: str


@dataclass
class FolderSeedResult:
    plan: AccessReviewPlan
    found: int = 0
    created: int = 0
    updated: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)
    parent_warnings: list[str] = field(default_factory=list)
    examples: list[FolderSeedItem] = field(default_factory=list)
    dry_run: bool = False


def normalize_path(value):
    value = (value or '').strip().replace('/', '\\')
    while '\\\\' in value:
        value = value.replace('\\\\', '\\')
    return value.strip('\\')


def strip_drive(value):
    value = normalize_path(value)
    drive = PureWindowsPath(value).drive
    if drive:
        value = value[len(drive):].strip('\\')
    return value


def path_segments(value):
    return [segment for segment in normalize_path(value).split('\\') if segment]


def logical_path_for_folder(folder, root_path=''):
    raw_path = strip_drive(folder.path)
    root_path = normalize_path(root_path)
    segments = path_segments(raw_path)

    if root_path:
        root_segments = path_segments(root_path)
        root_len = len(root_segments)
        for index in range(0, len(segments) - root_len + 1):
            if [item.lower() for item in segments[index:index + root_len]] == [item.lower() for item in root_segments]:
                return '\\'.join(segments[index:])
        return ''

    share_unc = normalize_path(getattr(folder.share, 'unc_path', ''))
    if share_unc and raw_path.lower().startswith(share_unc.lower()):
        return raw_path[len(share_unc):].strip('\\')

    return raw_path


def name_from_path(value):
    segments = path_segments(value)
    return segments[-1] if segments else 'Raiz'


def parent_path_from_path(value):
    segments = path_segments(value)
    if len(segments) <= 1:
        return ''
    return '\\'.join(segments[:-1])


def area_for_path(value, folder, root_path='', area_name='', area_mode='simple'):
    if area_name:
        return area_name
    if area_mode == 'general':
        return 'Geral'
    if area_mode == 'share':
        return folder.share.name or 'Geral'

    segments = path_segments(value)
    root_segments = path_segments(root_path)
    if root_segments and len(segments) > len(root_segments):
        return segments[len(root_segments)] or 'Geral'
    if segments:
        return segments[0]
    return 'Geral'


def build_folder_seed_items(folders, root_path='', area_name='', area_mode='simple'):
    items = []
    for source_folder in folders:
        proposed_path = logical_path_for_folder(source_folder, root_path=root_path)
        if not proposed_path:
            continue

        parent_path = parent_path_from_path(proposed_path)
        notes = (
            f'Snapshot atual: {source_folder.share.file_server.name} | '
            f'{source_folder.share.unc_path} | {source_folder.path}'
        )
        items.append(FolderSeedItem(
            source_folder=source_folder,
            proposed_path=proposed_path,
            name=name_from_path(proposed_path),
            area_name=area_for_path(proposed_path, source_folder, root_path=root_path, area_name=area_name, area_mode=area_mode),
            parent_path=parent_path,
            level=len(path_segments(proposed_path)),
            sort_order=0,
            notes=notes,
        ))

    items.sort(key=lambda item: (item.level, item.proposed_path.lower(), item.name.lower()))
    for index, item in enumerate(items, start=1):
        item.sort_order = index
    return items


def folders_for_seed(root_path='', share=''):
    queryset = Folder.objects.select_related('share', 'share__file_server').order_by('share__unc_path', 'path')
    if share:
        queryset = queryset.filter(Q(share__name__iexact=share) | Q(share__unc_path__iexact=share))
    return queryset


@transaction.atomic
def seed_access_review_folders(
    plan,
    root_path='',
    share='',
    area_name='',
    area_mode='simple',
    dry_run=False,
    replace=False,
    force_replace=False,
):
    result = FolderSeedResult(plan=plan, dry_run=dry_run)
    source_folders = folders_for_seed(root_path=root_path, share=share)
    items = build_folder_seed_items(source_folders, root_path=root_path, area_name=area_name, area_mode=area_mode)
    result.found = len(items)
    result.examples = items[:10]

    if replace:
        folders_with_rules = plan.folders.filter(rules__isnull=False).distinct().count()
        if folders_with_rules and not force_replace and not dry_run:
            result.errors.append(
                f'Replace bloqueado: {folders_with_rules} pastas planejadas possuem regras vinculadas.'
            )
            return result
        if not dry_run:
            plan.folders.all().delete()

    existing_by_current = {
        item.current_folder_id: item
        for item in plan.folders.select_related('current_folder')
        if item.current_folder_id
    }
    existing_by_path = {
        item.proposed_path.lower(): item
        for item in plan.folders.all()
    }

    if dry_run:
        for item in items:
            existing = existing_by_current.get(item.source_folder.id) or existing_by_path.get(item.proposed_path.lower())
            if existing:
                result.updated += 1
            else:
                result.created += 1
        for item in items:
            if item.parent_path and item.parent_path.lower() not in {candidate.proposed_path.lower() for candidate in items}:
                result.parent_warnings.append(f'Parent nao encontrado para {item.proposed_path}: {item.parent_path}')
        return result

    saved_by_path = {}
    for item in items:
        existing = existing_by_current.get(item.source_folder.id) or existing_by_path.get(item.proposed_path.lower())
        defaults = {
            'area_name': item.area_name,
            'name': item.name,
            'proposed_path': item.proposed_path,
            'current_folder': item.source_folder,
            'sort_order': item.sort_order,
            'notes': item.notes,
        }
        if existing:
            changed = False
            for field_name, value in defaults.items():
                if getattr(existing, field_name) != value:
                    setattr(existing, field_name, value)
                    changed = True
            if changed:
                existing.save(update_fields=[*defaults.keys(), 'updated_at'])
                result.updated += 1
            else:
                result.ignored += 1
            saved = existing
        else:
            saved = AccessReviewFolder.objects.create(plan=plan, **defaults)
            result.created += 1

        saved_by_path[item.proposed_path.lower()] = saved
        existing_by_current[item.source_folder.id] = saved
        existing_by_path[item.proposed_path.lower()] = saved

    for item in items:
        review_folder = saved_by_path.get(item.proposed_path.lower())
        parent = saved_by_path.get(item.parent_path.lower()) if item.parent_path else None
        if item.parent_path and parent is None:
            result.parent_warnings.append(f'Parent nao encontrado para {item.proposed_path}: {item.parent_path}')
        if review_folder and review_folder.parent_id != (parent.id if parent else None):
            review_folder.parent = parent
            review_folder.save(update_fields=['parent', 'updated_at'])

    return result

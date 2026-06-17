from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from django.db import transaction
from django.db.models import Count, Q

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
    dedup_warnings: list[str] = field(default_factory=list)
    existing_duplicate_warnings: list[str] = field(default_factory=list)
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


def acl_count_for_folder(folder):
    return getattr(folder, 'acl_count', None) or folder.acl_entries.count()


def dedup_reason(chosen, candidates, child_counts):
    chosen_children = child_counts.get(chosen.source_folder.id, 0)
    chosen_acl_count = acl_count_for_folder(chosen.source_folder)
    if chosen_children:
        return f'tem {chosen_children} filhos no conjunto filtrado'
    if chosen_acl_count:
        return f'tem {chosen_acl_count} ACLs'
    if chosen.source_folder.updated_at:
        return f'updated_at={chosen.source_folder.updated_at.isoformat()}'
    return f'maior id={chosen.source_folder.id}'


def choose_canonical_item(candidates, child_counts):
    return sorted(
        candidates,
        key=lambda item: (
            child_counts.get(item.source_folder.id, 0) > 0,
            acl_count_for_folder(item.source_folder),
            item.source_folder.updated_at,
            item.source_folder.id,
        ),
        reverse=True,
    )[0]


def deduplicate_folder_seed_items(items):
    by_path = {}
    for item in items:
        by_path.setdefault(item.proposed_path.lower(), []).append(item)

    child_counts = {item.source_folder.id: 0 for item in items}
    item_by_share_and_path = {
        (item.source_folder.share_id, normalize_path(item.source_folder.path).lower()): item
        for item in items
    }
    item_by_share_and_proposed_path = {
        (item.source_folder.share_id, item.proposed_path.lower()): item
        for item in items
    }
    for item in items:
        parent_path = normalize_path(item.source_folder.parent_path).lower()
        parent = (
            item_by_share_and_path.get((item.source_folder.share_id, parent_path))
            or item_by_share_and_proposed_path.get((item.source_folder.share_id, item.parent_path.lower()))
        )
        if parent:
            child_counts[parent.source_folder.id] = child_counts.get(parent.source_folder.id, 0) + 1

    canonical_items = []
    warnings = []
    for proposed_path, candidates in by_path.items():
        if len(candidates) == 1:
            canonical_items.append(candidates[0])
            continue
        chosen = choose_canonical_item(candidates, child_counts)
        ids = ', '.join(str(item.source_folder.id) for item in candidates)
        warnings.append(
            f'Path duplicado "{chosen.proposed_path}": ids encontrados=[{ids}], '
            f'id escolhido={chosen.source_folder.id}, motivo={dedup_reason(chosen, candidates, child_counts)}.'
        )
        canonical_items.append(chosen)

    canonical_items.sort(key=lambda item: (item.level, item.proposed_path.lower(), item.name.lower()))
    for index, item in enumerate(canonical_items, start=1):
        item.sort_order = index
    return canonical_items, warnings


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

    return items


def folders_for_seed(root_path='', share='', share_id=None):
    queryset = Folder.objects.select_related('share', 'share__file_server').annotate(
        acl_count=Count('acl_entries'),
    ).order_by('share__unc_path', 'path', 'id')
    if share_id:
        queryset = queryset.filter(share_id=share_id)
    if share:
        queryset = queryset.filter(Q(share__name__iexact=share) | Q(share__unc_path__iexact=share))
    return queryset


def existing_duplicate_warnings(plan):
    warnings = []
    duplicate_rows = (
        plan.folders.values('proposed_path')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .order_by('proposed_path')
    )
    for row in duplicate_rows:
        warnings.append(
            f'AccessReviewFolder duplicado ja existente no plano: proposed_path="{row["proposed_path"]}" total={row["total"]}. '
            'Nao foi removido automaticamente.'
        )
    return warnings


@transaction.atomic
def seed_access_review_folders(
    plan,
    root_path='',
    share='',
    share_id=None,
    area_name='',
    area_mode='simple',
    dry_run=False,
    replace=False,
    force_replace=False,
):
    result = FolderSeedResult(plan=plan, dry_run=dry_run)
    source_folders = folders_for_seed(root_path=root_path, share=share, share_id=share_id)
    raw_items = build_folder_seed_items(source_folders, root_path=root_path, area_name=area_name, area_mode=area_mode)
    items, dedup_warnings = deduplicate_folder_seed_items(raw_items)
    result.dedup_warnings = dedup_warnings
    result.existing_duplicate_warnings = existing_duplicate_warnings(plan)
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

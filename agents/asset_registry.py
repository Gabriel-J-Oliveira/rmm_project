"""Explicit, audited links between physical assets and agent installations."""

from django.db import transaction
from django.utils import timezone

from .identity import meaningful_serial, normalized_machine_id
from .models import AgentMachine, AssetAgentLink, AssetRecord, AuditEvent, InventorySnapshot, PhysicalAsset


class AssetRegistryError(ValueError):
    def __init__(self, code, *, status=409, details=None):
        self.code = code
        self.status = status
        self.details = details or {}
        super().__init__(code)


def inventory_evidence(endpoint):
    snapshot = InventorySnapshot.objects.filter(machine_id=endpoint.pk).order_by('-received_at', '-pk').first()
    payload = snapshot.raw_payload if snapshot and isinstance(snapshot.raw_payload, dict) else {}
    collections = payload.get('collections') if isinstance(payload.get('collections'), dict) else {}
    full = collections.get('full_inventory') if isinstance(collections.get('full_inventory'), dict) else {}
    system = collections.get('system') if isinstance(collections.get('system'), dict) else {}
    if not system and isinstance(full.get('system'), dict):
        system = full['system']
    hardware = collections.get('hardware') if isinstance(collections.get('hardware'), dict) else {}
    if not hardware and isinstance(full.get('hardware'), dict):
        hardware = full['hardware']
    bios = hardware.get('bios') if isinstance(hardware.get('bios'), dict) else {}
    serial_values = [endpoint.serial_number]
    serial_values.extend([system.get('serial_number'), hardware.get('serial_number'), bios.get('serial_number'),
                          full.get('serial_number')])
    serial_normalized = {meaningful_serial(value) for value in serial_values if meaningful_serial(value)}
    conflict = len(serial_normalized) > 1
    normalized = next(iter(serial_normalized)) if len(serial_normalized) == 1 and not conflict else None
    serial_display = (system.get('serial_number') or bios.get('serial_number') or
                      hardware.get('serial_number') or (snapshot.serial_number if snapshot else '') or endpoint.serial_number)
    manufacturer = (system.get('manufacturer') or hardware.get('manufacturer') or
                    (snapshot.manufacturer if snapshot else '') or endpoint.manufacturer or '').strip()
    model = (system.get('model') or hardware.get('model') or
             (snapshot.model if snapshot else '') or endpoint.model or '').strip()
    return {
        'serial_number': str(serial_display or '').strip(),
        'serial_normalized': normalized,
        'serial_conflict': conflict,
        'manufacturer': manufacturer,
        'model': model,
        'inventory_received_at': snapshot.received_at if snapshot else None,
        'inventory_collected_at': snapshot.collected_at if snapshot else None,
    }


def endpoint_identity_warnings(endpoint):
    warnings = []
    machine_id = ''
    try:
        machine_id = normalized_machine_id(endpoint.machine_id)
    except ValueError:
        warnings.append({'code': 'machine_id_invalid', 'message': 'Machine ID inválido; confirme o endpoint pelo UUID.'})
    if not machine_id:
        warnings.append({'code': 'machine_id_missing', 'message': 'Machine ID ausente; confirme o endpoint pelo UUID.'})
    else:
        from .identity import machine_id_matches
        if machine_id_matches(machine_id).count() > 1:
            warnings.append({'code': 'machine_id_ambiguous', 'message': 'Machine ID aparece em mais de um endpoint; confirme manualmente o UUID selecionado.'})
    hostname_count = AgentMachine.objects.filter(
        hostname__iexact=endpoint.hostname, domain__iexact=endpoint.domain,
    ).count()
    if hostname_count > 1:
        warnings.append({'code': 'hostname_duplicated', 'message': 'Hostname duplicado. Ele não será usado para associar patrimônios.'})
    return warnings


def _normalized_text(value):
    return ' '.join(str(value or '').split()).casefold()


def _serial_endpoint_conflicts(endpoint, serial_normalized):
    if not serial_normalized:
        return []
    conflicts = []
    rows = AgentMachine.objects.filter(is_active=True).exclude(pk=endpoint.pk).exclude(serial_number='').only(
        'id', 'hostname', 'serial_number', 'status', 'machine_id',
    )
    for row in rows.iterator(chunk_size=500):
        if meaningful_serial(row.serial_number) == serial_normalized:
            conflicts.append({
                'endpoint_id': str(row.pk), 'hostname': row.hostname, 'status': row.status,
            })
    return conflicts


def _audit(event_type, title, endpoint, user, metadata, description=''):
    return AuditEvent.objects.create(
        event_type=event_type, severity=AuditEvent.SEVERITY_INFO,
        actor_type=AuditEvent.ACTOR_USER, actor_name=user.get_username(), endpoint=endpoint,
        title=title, description=description[:2000], metadata=metadata,
    )


def _asset_summary(asset):
    return {
        'id': str(asset.pk), 'code': asset.asset_code, 'status': asset.status,
        'identity_status': asset.identity_status, 'serial_number': asset.serial_number,
        'manufacturer': asset.manufacturer, 'model': asset.model,
    }


def endpoint_asset_payload(endpoint):
    evidence = inventory_evidence(endpoint)
    warnings = endpoint_identity_warnings(endpoint)
    current = list(AssetAgentLink.objects.filter(
        endpoint_id=endpoint.pk, ended_at__isnull=True,
    ).select_related('asset').order_by('-linked_at')[:2])
    if len(current) > 1:
        return {'state': 'identity_conflict', 'error': 'multiple_active_asset_links', 'inventory': evidence,
                'identity_warnings': warnings, 'asset': None, 'records': []}
    if current:
        link = current[0]
        evidence_conflicts = []
        if evidence['serial_conflict']:
            evidence_conflicts.append({'code': 'inventory_serial_conflict',
                                       'message': 'O inventário atual contém valores de serial divergentes.'})
        elif (link.asset.serial_normalized and evidence['serial_normalized']
              and link.asset.serial_normalized != evidence['serial_normalized']):
            evidence_conflicts.append({'code': 'asset_serial_mismatch',
                                       'message': 'O serial observado difere do serial registrado no patrimônio.'})
        if (link.asset.manufacturer and evidence['manufacturer']
                and _normalized_text(link.asset.manufacturer) != _normalized_text(evidence['manufacturer'])):
            evidence_conflicts.append({'code': 'asset_manufacturer_mismatch',
                                       'message': 'O fabricante observado difere do registrado no patrimônio.'})
        if (link.asset.model and evidence['model']
                and _normalized_text(link.asset.model) != _normalized_text(evidence['model'])):
            evidence_conflicts.append({'code': 'asset_model_mismatch',
                                       'message': 'O modelo observado difere do registrado no patrimônio.'})
        records = AssetRecord.objects.filter(asset=link.asset).select_related('author').order_by('occurred_at', 'created_at')
        return {
            'state': 'linked', 'asset': _asset_summary(link.asset),
            'current_link': {
                'id': str(link.pk), 'linked_at': link.linked_at.isoformat(), 'origin': link.origin,
                'endpoint_id': str(link.endpoint_uuid_snapshot), 'machine_id': link.machine_id_snapshot,
                'hostname': link.hostname_snapshot, 'reason': link.reason,
            },
            'links': [{
                'endpoint_id': str(row.endpoint_uuid_snapshot), 'hostname': row.hostname_snapshot,
                'machine_id': row.machine_id_snapshot, 'linked_at': row.linked_at.isoformat(),
                'ended_at': row.ended_at.isoformat() if row.ended_at else None,
                'reason': row.reason, 'current': row.ended_at is None and row.endpoint_id is not None,
            } for row in link.asset.agent_links.order_by('linked_at', 'pk')],
            'records': [{
                'id': str(row.pk), 'type': row.record_type,
                'label': row.get_record_type_display(), 'description': row.description,
                'occurred_at': row.occurred_at.isoformat(), 'created_at': row.created_at.isoformat(),
                'author': row.author_label or (row.author.get_username() if row.author else 'Autor removido'),
                'previous_responsible': row.previous_responsible, 'new_responsible': row.new_responsible,
            } for row in records],
            'inventory': evidence, 'identity_warnings': warnings, 'evidence_conflicts': evidence_conflicts,
        }

    serial_conflict = evidence['serial_conflict']
    serial_matches = []
    if evidence['serial_normalized']:
        serial_matches = list(PhysicalAsset.objects.filter(
            status=PhysicalAsset.STATUS_ACTIVE, serial_normalized=evidence['serial_normalized'],
        ).order_by('asset_code')[:2])
    suggestion = serial_matches[0] if len(serial_matches) == 1 and not serial_conflict else None
    suggestion_link = None
    if suggestion:
        suggestion_link = AssetAgentLink.objects.filter(asset=suggestion, ended_at__isnull=True).select_related(
            'endpoint',
        ).first()
    mismatch = bool(suggestion and (
        (suggestion.manufacturer and evidence['manufacturer'] and
         _normalized_text(suggestion.manufacturer) != _normalized_text(evidence['manufacturer']))
        or (suggestion.model and evidence['model'] and
            _normalized_text(suggestion.model) != _normalized_text(evidence['model']))
    ))
    return {
        'state': 'conflict' if serial_conflict or len(serial_matches) > 1 or mismatch else 'unlinked',
        'asset': None,
        'inventory': evidence,
        'identity_warnings': warnings,
        'serial_conflict': serial_conflict or len(serial_matches) > 1,
        # Scanning every endpoint to compare legacy raw serial values is an
        # explicit-write preflight, not part of this read endpoint. Keep the
        # detail page bounded; create_asset_for_endpoint performs that check
        # under the transaction and returns the conflicting observations.
        'matching_endpoint_observations': [],
        'suggestion': _asset_summary(suggestion) if suggestion else None,
        'suggestion_active_link': ({
            'endpoint_id': str(suggestion_link.endpoint_uuid_snapshot),
            'hostname': suggestion_link.hostname_snapshot,
            'machine_id': suggestion_link.machine_id_snapshot,
        } if suggestion_link else None),
        'suggestion_evidence_mismatch': mismatch,
        'suggestion_requires_transfer': bool(suggestion_link and suggestion_link.endpoint_id != endpoint.pk),
    }


def _lock_endpoint(endpoint_id, expected_machine_id=None):
    try:
        endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    except AgentMachine.DoesNotExist as exc:
        raise AssetRegistryError('endpoint_not_found', status=404) from exc
    if not endpoint.is_active:
        raise AssetRegistryError('endpoint_inactive')
    if expected_machine_id is None:
        raise AssetRegistryError('endpoint_identity_required', status=400)
    expected = str(expected_machine_id or '').strip()
    if expected.casefold() != str(endpoint.machine_id or '').strip().casefold():
        raise AssetRegistryError('endpoint_identity_changed', status=409)
    return endpoint


def _identity_resolution_reason(endpoint, reason):
    warnings = endpoint_identity_warnings(endpoint)
    requires_reason = any(item['code'] in {'machine_id_invalid', 'machine_id_missing', 'machine_id_ambiguous'}
                          for item in warnings)
    reason = str(reason or '').strip()
    if requires_reason and len(reason) < 8:
        raise AssetRegistryError('identity_resolution_required', details={'warnings': warnings})
    return warnings, reason


def create_asset_for_endpoint(endpoint_id, expected_machine_id, user, *, identity_confirmed, reason=''):
    if not identity_confirmed:
        raise AssetRegistryError('explicit_confirmation_required', status=400)
    with transaction.atomic():
        endpoint = _lock_endpoint(endpoint_id, expected_machine_id)
        evidence = inventory_evidence(endpoint)
        warnings, reason = _identity_resolution_reason(endpoint, reason)
        if AssetAgentLink.objects.select_for_update().filter(endpoint=endpoint, ended_at__isnull=True).exists():
            raise AssetRegistryError('endpoint_already_linked')
        if evidence['serial_conflict']:
            raise AssetRegistryError('inventory_serial_conflict', details={'warnings': warnings})
        serial = evidence['serial_normalized']
        if serial and PhysicalAsset.objects.select_for_update().filter(
                status=PhysicalAsset.STATUS_ACTIVE, serial_normalized=serial).exists():
            raise AssetRegistryError('serial_matches_existing_asset', details={'suggestion_available': True})
        observed_duplicates = _serial_endpoint_conflicts(endpoint, serial)
        if observed_duplicates and len(reason) < 8:
            raise AssetRegistryError('duplicate_endpoint_serial_requires_review', details={
                'matching_endpoint_observations': observed_duplicates,
            })
        asset = PhysicalAsset.objects.create(
            serial_number=evidence['serial_number'], serial_normalized=serial,
            manufacturer=evidence['manufacturer'], model=evidence['model'], created_by=user,
            identity_status=(PhysicalAsset.IDENTITY_REVIEW if observed_duplicates or warnings else
                             PhysicalAsset.IDENTITY_CONFIRMED if serial else PhysicalAsset.IDENTITY_PROVISIONAL),
        )
        link = AssetAgentLink.objects.create(
            asset=asset, endpoint=endpoint, endpoint_uuid_snapshot=endpoint.pk,
            machine_id_snapshot=endpoint.machine_id or '', hostname_snapshot=endpoint.hostname or '',
            origin=AssetAgentLink.ORIGIN_OPERATOR,
            evidence={'serial_normalized': serial or None, 'manufacturer': evidence['manufacturer'],
                      'model': evidence['model'], 'inventory_received_at':
                      evidence['inventory_received_at'].isoformat() if evidence['inventory_received_at'] else None},
            reason=reason, linked_by=user,
        )
        _audit('asset.created', 'Patrimônio provisório criado', endpoint, user,
               {'asset_id': str(asset.pk), 'asset_code': asset.asset_code, 'link_id': str(link.pk),
                'identity_status': asset.identity_status, 'identity_warnings': [row['code'] for row in warnings]}, reason)
        return asset


def link_asset_to_endpoint(endpoint_id, expected_machine_id, asset_id, user, *, identity_confirmed,
                            transfer_confirmed=False, reason=''):
    if not identity_confirmed:
        raise AssetRegistryError('explicit_confirmation_required', status=400)
    reason = str(reason or '').strip()
    with transaction.atomic():
        endpoint = _lock_endpoint(endpoint_id, expected_machine_id)
        try:
            asset = PhysicalAsset.objects.select_for_update().get(pk=asset_id, status=PhysicalAsset.STATUS_ACTIVE)
        except PhysicalAsset.DoesNotExist as exc:
            raise AssetRegistryError('asset_not_found', status=404) from exc
        evidence = inventory_evidence(endpoint)
        warnings, reason = _identity_resolution_reason(endpoint, reason)
        if asset.identity_status == PhysicalAsset.IDENTITY_REVIEW and len(reason) < 8:
            raise AssetRegistryError('identity_resolution_required', details={'warnings': warnings})
        observed_duplicates = _serial_endpoint_conflicts(endpoint, evidence['serial_normalized'])
        if observed_duplicates and len(reason) < 8:
            raise AssetRegistryError('duplicate_endpoint_serial_requires_review', details={
                'matching_endpoint_observations': observed_duplicates,
            })
        discrepancies = []
        if evidence['serial_conflict']:
            discrepancies.append('inventory_serial_conflict')
        if evidence['serial_normalized'] and asset.serial_normalized and evidence['serial_normalized'] != asset.serial_normalized:
            discrepancies.append('serial_mismatch')
        if asset.manufacturer and evidence['manufacturer'] and _normalized_text(asset.manufacturer) != _normalized_text(evidence['manufacturer']):
            discrepancies.append('manufacturer_mismatch')
        if asset.model and evidence['model'] and _normalized_text(asset.model) != _normalized_text(evidence['model']):
            discrepancies.append('model_mismatch')
        if discrepancies and len(reason) < 8:
            raise AssetRegistryError('evidence_resolution_required', details={'discrepancies': discrepancies})

        endpoint_link = AssetAgentLink.objects.select_for_update().filter(
            endpoint=endpoint, ended_at__isnull=True,
        ).select_related('asset').first()
        if endpoint_link and endpoint_link.asset_id == asset.pk:
            return asset
        asset_link = AssetAgentLink.objects.select_for_update().filter(
            asset=asset, ended_at__isnull=True,
        ).first()
        has_transfer = bool((endpoint_link and endpoint_link.asset_id != asset.pk)
                            or (asset_link and asset_link.endpoint_uuid_snapshot != endpoint.pk))
        if has_transfer and (not transfer_confirmed or len(reason) < 8):
            raise AssetRegistryError('link_transfer_confirmation_required', details={
                'current_asset_code': endpoint_link.asset.asset_code if endpoint_link else None,
                'current_endpoint_id': str(asset_link.endpoint_uuid_snapshot) if asset_link else None,
            })
        now = timezone.now()
        if endpoint_link:
            endpoint_link.ended_at = now
            endpoint_link.reason = (endpoint_link.reason + '\n' if endpoint_link.reason else '') + f'Vínculo encerrado: {reason}'
            endpoint_link.save(update_fields=['ended_at', 'reason'])
        if asset_link:
            asset_link.ended_at = now
            asset_link.reason = (asset_link.reason + '\n' if asset_link.reason else '') + f'Vínculo encerrado: {reason}'
            asset_link.save(update_fields=['ended_at', 'reason'])
        if not asset.serial_normalized and evidence['serial_normalized']:
            asset.serial_number = evidence['serial_number']
            asset.serial_normalized = evidence['serial_normalized']
        if discrepancies or warnings:
            asset.identity_status = PhysicalAsset.IDENTITY_REVIEW
        elif asset.serial_normalized:
            asset.identity_status = PhysicalAsset.IDENTITY_CONFIRMED
        asset.save(update_fields=['serial_number', 'serial_normalized', 'identity_status', 'updated_at'])
        link = AssetAgentLink.objects.create(
            asset=asset, endpoint=endpoint, endpoint_uuid_snapshot=endpoint.pk,
            machine_id_snapshot=endpoint.machine_id or '', hostname_snapshot=endpoint.hostname or '',
            origin=AssetAgentLink.ORIGIN_SERIAL_SUGGESTION if evidence['serial_normalized'] == asset.serial_normalized
                  else AssetAgentLink.ORIGIN_OPERATOR,
            evidence={'serial_normalized': evidence['serial_normalized'], 'manufacturer': evidence['manufacturer'],
                      'model': evidence['model'], 'discrepancies': discrepancies,
                      'inventory_received_at': evidence['inventory_received_at'].isoformat()
                      if evidence['inventory_received_at'] else None},
            reason=reason, linked_by=user,
        )
        _audit('asset.agent_linked', 'Agente associado ao patrimônio', endpoint, user,
               {'asset_id': str(asset.pk), 'asset_code': asset.asset_code, 'link_id': str(link.pk),
                'previous_endpoint_id': str(asset_link.endpoint_uuid_snapshot) if asset_link else None,
                'closed_asset_id': str(endpoint_link.asset_id) if endpoint_link else None,
                'discrepancies': discrepancies, 'identity_warnings': [row['code'] for row in warnings]}, reason)
        return asset


def add_asset_record(endpoint_id, expected_machine_id, user, *, record_type, description, occurred_at,
                     previous_responsible='', new_responsible=''):
    with transaction.atomic():
        endpoint = _lock_endpoint(endpoint_id, expected_machine_id)
        link = AssetAgentLink.objects.select_for_update().filter(
            endpoint=endpoint, ended_at__isnull=True,
        ).select_related('asset').first()
        if not link:
            raise AssetRegistryError('asset_not_linked_to_endpoint')
        if str(link.machine_id_snapshot or '').strip().casefold() != str(endpoint.machine_id or '').strip().casefold():
            raise AssetRegistryError('asset_link_identity_changed', details={
                'asset_id': str(link.asset_id), 'asset_code': link.asset.asset_code,
            })
        if record_type not in dict(AssetRecord.TYPE_CHOICES):
            raise AssetRegistryError('record_type_invalid', status=400)
        description = str(description or '').strip()
        if not description or len(description) > 5000:
            raise AssetRegistryError('record_description_invalid', status=400)
        if record_type == AssetRecord.TYPE_RESPONSIBILITY_CHANGE and not (
                str(previous_responsible or '').strip() and str(new_responsible or '').strip()):
            raise AssetRegistryError('responsible_values_required', status=400)
        record = AssetRecord.objects.create(
            asset=link.asset, record_type=record_type, description=description,
            occurred_at=occurred_at, previous_responsible=str(previous_responsible or '').strip()[:255],
            new_responsible=str(new_responsible or '').strip()[:255], author=user,
            author_label=user.get_username()[:150],
        )
        _audit('asset.record_created', 'Registro patrimonial incluído', endpoint, user,
               {'asset_id': str(link.asset_id), 'asset_code': link.asset.asset_code,
                'record_id': str(record.pk), 'record_type': record.record_type}, description)
        return record

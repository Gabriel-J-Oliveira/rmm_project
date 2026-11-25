"""Reconcile fresh, authenticated inventory with permanent physical assets."""

import re
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .asset_registry import AssetRegistryError, _audit, _lock_endpoint, _normalized_text, link_matches_endpoint
from .identity import lock_identity_registry, machine_id_matches, meaningful_serial, normalized_machine_id
from .models import AgentMachine, AssetAgentLink, AssetHardwareObservation, AuditEvent, InventorySnapshot, PhysicalAsset


VIRTUAL_MODEL = re.compile(
    r'virtual|vmware|virtualbox|qemu|kvm|xen|parallels|bhyve|bochs|hvm|hyper-v|amazon ec2|google compute|openstack|nutanix',
    re.I,
)
TERMINAL_LIFECYCLES = ('uninstalled', 'purged')


def _object(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value.strip()[:255] if isinstance(value, str) else ''


def normalized_smbios_uuid(value):
    try:
        result = UUID(_text(value))
    except ValueError:
        return None
    digits = result.hex
    if len(set(digits)) == 1 or digits in {
        '03000200040005000006000700080009', '00020003000400050006000700080009',
        '123456781234123412341234567890ab',
    }:
        return None
    return result


def hardware_evidence(hardware):
    product = _object(hardware.get('oem_product'))
    bios = _object(hardware.get('bios'))
    serials = [_text(bios.get('serial_number')), _text(hardware.get('serial_number'))]
    valid_serials = {meaningful_serial(value): value for value in serials if meaningful_serial(value)}
    raw_uuid = _text(product.get('smbios_uuid'))
    evidence = {
        'smbios_uuid': raw_uuid, 'serial_number': next(iter(valid_serials.values()), ''),
        'manufacturer': _text(hardware.get('manufacturer')), 'model': _text(hardware.get('model')),
    }
    key = normalized_smbios_uuid(raw_uuid)
    if key is None:
        return key, evidence, 'uuid_invalid' if raw_uuid else 'uuid_missing'
    if len(valid_serials) > 1:
        return key, evidence, 'serial_conflict'
    if (product.get('hypervisor_present') is not False or not evidence['manufacturer'] or not evidence['model']
            or VIRTUAL_MODEL.search(evidence['manufacturer'] + ' ' + evidence['model'])):
        return key, evidence, 'physical_unconfirmed'
    return key, evidence, 'eligible'


def live_claims(key, endpoint_id):
    return AssetHardwareObservation.objects.filter(
        smbios_uuid=key, endpoint__is_active=True,
    ).exclude(endpoint_id=endpoint_id).exclude(endpoint__agent_lifecycle_status__in=TERMINAL_LIFECYCLES)


def _matches_evidence(asset, evidence):
    serial = meaningful_serial(evidence['serial_number'])
    if serial and asset.serial_normalized and serial != asset.serial_normalized:
        return False
    return all(
        not getattr(asset, field) or _normalized_text(getattr(asset, field)) == _normalized_text(evidence[field])
        for field in ('manufacturer', 'model')
    )


def _replaced_uuid(key):
    return AssetAgentLink.objects.filter(ended_at__isnull=False, evidence__replaced_smbios_uuid=str(key)).exists()


def _reconcile(endpoint, key, evidence, snapshot):
    try:
        machine_id = normalized_machine_id(endpoint.machine_id)
    except ValueError:
        machine_id = ''
    if not machine_id or machine_id_matches(machine_id).exclude(pk=endpoint.pk).exists():
        return 'agent_identity_conflict'
    if live_claims(key, endpoint.pk).exists():
        return 'uuid_duplicate'
    current = AssetAgentLink.objects.filter(endpoint=endpoint, ended_at__isnull=True).first()
    if current and not link_matches_endpoint(current, endpoint):
        return 'agent_identity_conflict'
    assets = PhysicalAsset.objects.select_for_update()
    candidate = assets.filter(smbios_uuid=key).first()
    if not candidate and _replaced_uuid(key):
        return 'asset_identity_conflict'
    serial = meaningful_serial(evidence['serial_number'])
    by_serial = assets.filter(status=PhysicalAsset.STATUS_ACTIVE, serial_normalized=serial).first() if serial else None
    if candidate and (candidate.status != PhysicalAsset.STATUS_ACTIVE or (by_serial and by_serial.pk != candidate.pk)):
        return 'asset_identity_conflict'
    if not candidate and current:
        linked_asset = assets.get(pk=current.asset_id)
        if (linked_asset.smbios_uuid or linked_asset.status != PhysicalAsset.STATUS_ACTIVE
                or (by_serial and by_serial.pk != linked_asset.pk)):
            return 'asset_identity_conflict'
        candidate = linked_asset
    if not candidate and by_serial:
        # Adopting a UUID for an older asset on a new installation needs more
        # than a serial, plus a prior association of that same asset.
        if (by_serial.smbios_uuid or not by_serial.manufacturer or not by_serial.model
                or not by_serial.agent_links.exists()):
            return 'asset_identity_conflict'
        candidate = by_serial
    if current and (not candidate or candidate.pk != current.asset_id):
        return 'asset_identity_conflict'
    if candidate and (candidate.identity_status == PhysicalAsset.IDENTITY_REVIEW
                      or not _matches_evidence(candidate, evidence)):
        return 'asset_identity_conflict'
    previous = None
    if candidate:
        previous = AssetAgentLink.objects.filter(asset=candidate, ended_at__isnull=True).first()
        if previous and previous.endpoint_id and not current:
            old = AgentMachine.objects.filter(pk=previous.endpoint_id).first()
            if old and old.is_active and not old.has_terminal_lifecycle:
                return 'previous_agent_active'
        if candidate.smbios_uuid is None:
            candidate.smbios_uuid = key
            candidate.save(update_fields=['smbios_uuid', 'updated_at'])
            AuditEvent.objects.create(
                event_type='asset.smbios_adopted', actor_type=AuditEvent.ACTOR_SYSTEM, endpoint=endpoint,
                title='UUID SMBIOS confirmado no patrimônio existente',
                metadata={'asset_id': str(candidate.pk), 'snapshot_id': str(snapshot.pk), 'smbios_uuid': str(key)},
            )
    else:
        candidate = PhysicalAsset.objects.create(
            smbios_uuid=key, serial_number=evidence['serial_number'], manufacturer=evidence['manufacturer'],
            model=evidence['model'], identity_status=PhysicalAsset.IDENTITY_CONFIRMED,
        )
    if current:
        return 'linked'
    now = timezone.now()
    if previous:
        previous.ended_at = now
        previous.save(update_fields=['ended_at'])
    link = AssetAgentLink.objects.create(
        asset=candidate, endpoint=endpoint, endpoint_uuid_snapshot=endpoint.pk,
        machine_id_snapshot=endpoint.machine_id, hostname_snapshot=endpoint.hostname,
        origin=AssetAgentLink.ORIGIN_SMBIOS, evidence={**evidence, 'snapshot_id': str(snapshot.pk)},
    )
    AuditEvent.objects.create(
        event_type='asset.smbios_linked', actor_type=AuditEvent.ACTOR_SYSTEM, endpoint=endpoint,
        title='Vínculo patrimonial validado por SMBIOS',
        metadata={'asset_id': str(candidate.pk), 'link_id': str(link.pk),
                  'previous_endpoint_id': str(previous.endpoint_uuid_snapshot) if previous else None},
    )
    return 'linked'


def board_change_candidate(endpoint, observation):
    if (not observation or observation.state != 'asset_identity_conflict'
            or observation.observed_at < timezone.now() - timedelta(days=1)
            or not normalized_smbios_uuid(str(observation.smbios_uuid or ''))
            or live_claims(observation.smbios_uuid, endpoint.pk).exists()
            or _replaced_uuid(observation.smbios_uuid)
            or PhysicalAsset.objects.filter(smbios_uuid=observation.smbios_uuid).exists()):
        return None
    link = AssetAgentLink.objects.filter(endpoint=endpoint, ended_at__isnull=True,
                                        asset__status=PhysicalAsset.STATUS_ACTIVE).select_related('asset').first()
    if not link or not link_matches_endpoint(link, endpoint) or not link.asset.smbios_uuid:
        return None
    asset = link.asset
    if (not asset.serial_normalized or meaningful_serial(observation.evidence.get('serial_number')) != asset.serial_normalized
            or not asset.manufacturer or not asset.model or not _matches_evidence(asset, observation.evidence)):
        return None
    return link


@transaction.atomic
def confirm_board_change(endpoint_id, expected_machine_id, snapshot_id, user, *, reason):
    reason = reason.strip() if isinstance(reason, str) else ''
    if not 8 <= len(reason) <= 2000:
        raise AssetRegistryError('identity_resolution_required')
    lock_identity_registry()
    endpoint = _lock_endpoint(endpoint_id, expected_machine_id)
    try:
        machine_id = normalized_machine_id(endpoint.machine_id)
    except ValueError:
        machine_id = ''
    if (endpoint.has_terminal_lifecycle or not machine_id
            or machine_id_matches(machine_id).exclude(pk=endpoint.pk).exists()):
        raise AssetRegistryError('asset_validation_identity_conflict')
    observation = AssetHardwareObservation.objects.filter(endpoint=endpoint, snapshot_id=snapshot_id).first()
    link = board_change_candidate(endpoint, observation)
    if not link:
        raise AssetRegistryError('hardware_change_not_confirmable')
    asset = PhysicalAsset.objects.select_for_update().get(pk=link.asset_id)
    old_uuid = asset.smbios_uuid
    now = timezone.now()
    link.ended_at = now
    link.evidence = {**link.evidence, 'replaced_smbios_uuid': str(old_uuid)}
    link.save(update_fields=['ended_at', 'evidence'])
    asset.smbios_uuid = observation.smbios_uuid
    asset.save(update_fields=['smbios_uuid', 'updated_at'])
    AssetAgentLink.objects.create(
        asset=asset, endpoint=endpoint, endpoint_uuid_snapshot=endpoint.pk, machine_id_snapshot=endpoint.machine_id,
        hostname_snapshot=endpoint.hostname, origin=AssetAgentLink.ORIGIN_SMBIOS,
        evidence={**observation.evidence, 'snapshot_id': str(snapshot_id)}, linked_by=user, reason=reason,
    )
    observation.state = 'linked'
    observation.save(update_fields=['state'])
    _audit('asset.board_change_confirmed', 'Troca de placa confirmada', endpoint, user,
           {'asset_id': str(asset.pk), 'previous_smbios_uuid': str(old_uuid),
            'smbios_uuid': str(asset.smbios_uuid), 'snapshot_id': str(snapshot_id)}, reason)


@transaction.atomic
def observe_hardware(endpoint_id, snapshot, hardware, *, reconcile=True):
    lock_identity_registry()
    endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    if not endpoint.is_active or endpoint.has_terminal_lifecycle:
        return None
    key, evidence, state = hardware_evidence(hardware)
    previous = AssetHardwareObservation.objects.filter(endpoint=endpoint).first()
    if previous and (previous.observed_at, previous.snapshot_id) >= (snapshot.received_at, snapshot.pk):
        return previous
    if state == 'eligible' and reconcile and settings.NIGHTOWL_AUTOMATIC_ASSETS_ENABLED:
        state = _reconcile(endpoint, key, evidence, snapshot)
    observation, _ = AssetHardwareObservation.objects.update_or_create(
        endpoint=endpoint, defaults={'smbios_uuid': key, 'evidence': evidence, 'snapshot_id': snapshot.pk,
                                     'observed_at': snapshot.received_at, 'state': state},
    )
    return observation


@transaction.atomic
def reconcile_stored_observation(endpoint_id):
    lock_identity_registry()
    endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
    if endpoint is None or not endpoint.is_active or endpoint.has_terminal_lifecycle:
        return 'endpoint_inactive'
    observation = AssetHardwareObservation.objects.filter(endpoint=endpoint, state='eligible').first()
    if observation is None:
        return 'observation_changed'
    snapshot = InventorySnapshot.objects.filter(pk=observation.snapshot_id, machine=endpoint).first()
    if snapshot is None:
        return 'inventory_not_observed'
    state = _reconcile(endpoint, observation.smbios_uuid, observation.evidence, snapshot)
    observation.state = state
    observation.save(update_fields=['state'])
    return state

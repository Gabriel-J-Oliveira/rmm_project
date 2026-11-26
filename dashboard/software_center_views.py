from datetime import timedelta

from django.http import HttpResponseForbidden, JsonResponse
from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from agents.models import AgentMachine, SoftwarePackageRecord
from config.authz import is_nightowl_technical_user


def _authorized(request):
    return bool(request.user.is_authenticated and request.user.is_active and is_nightowl_technical_user(request.user))


@never_cache
def software_center(request):
    if not _authorized(request):
        return HttpResponseForbidden('Acesso restrito a operadores técnicos.')
    return render(request, 'dashboard/software_inventory.html', {
        'active_nav': 'software',
        'inventory_api_url': reverse('software-inventory-data'),
        'packages_api_url': reverse('software-packages-data'),
    })


@require_GET
@never_cache
def software_inventory_data(request):
    if not _authorized(request):
        return JsonResponse({'error': 'forbidden'}, status=403)
    from dashboard.views import build_software_inventory, latest_snapshot_rows

    latest_rows = latest_snapshot_rows(active_only=True)
    rows, _summary = build_software_inventory(snapshot_rows=latest_rows)
    now = timezone.now()
    stale_after = timedelta(hours=max(1, int(getattr(settings, 'NIGHTOWL_SOFTWARE_INVENTORY_STALE_HOURS', 24))))
    endpoints_by_id = {}
    for endpoint, snapshot in latest_rows:
        age = now - snapshot.collected_at
        if age < timedelta(0):
            inventory_state = 'unknown'
        else:
            inventory_state = 'stale' if age > stale_after else 'current'
        endpoints_by_id[str(endpoint.id)] = {
            'id': str(endpoint.id),
            'hostname': endpoint.hostname,
            'inventory_at': snapshot.collected_at.isoformat(),
            'received_at': snapshot.received_at.isoformat(),
            'inventory_state': inventory_state,
        }

    query = str(request.GET.get('q', '')).strip().casefold()
    publisher_filter = str(request.GET.get('publisher', '')).strip().casefold()
    items = []
    for row in rows:
        related_endpoints = []
        for related in row['endpoints']:
            endpoint_id = str(related['endpoint'].id)
            related_endpoints.append({
                **endpoints_by_id.get(endpoint_id, {
                    'id': endpoint_id,
                    'hostname': related['endpoint'].hostname,
                    'inventory_at': related['snapshot'].collected_at.isoformat(),
                    'received_at': related['snapshot'].received_at.isoformat(),
                    'inventory_state': 'unknown',
                }),
                'version': related['version'] or None,
            })
        haystack = ' '.join([
            row['name'], row['publisher'], *row['versions'],
            *(endpoint['hostname'] for endpoint in related_endpoints),
        ]).casefold()
        if query and query not in haystack:
            continue
        if publisher_filter and publisher_filter != row['publisher'].casefold():
            continue
        freshness = [endpoint['inventory_state'] for endpoint in related_endpoints]
        items.append({
            'key': row['key'],
            'name': row['name'],
            'publisher': row['publisher'],
            'versions': row['versions'],
            'endpoint_count': row['endpoint_count'],
            'latest_seen_at': row['latest_seen_at'].isoformat(),
            'latest_collected_at': row['latest_collected_at'].isoformat(),
            'current_count': freshness.count('current'),
            'stale_count': freshness.count('stale'),
            'unknown_count': freshness.count('unknown'),
            'endpoints': sorted(related_endpoints, key=lambda item: item['hostname'].casefold()),
        })

    active_endpoint_count = AgentMachine.objects.filter(is_active=True).count()
    return JsonResponse({
        'items': items,
        'summary': {
            'software_count': len(items),
            'install_count': sum(item['endpoint_count'] for item in items),
            'endpoint_count': active_endpoint_count,
            'endpoints_with_inventory': sum(1 for endpoint, _ in latest_rows if endpoint.is_active),
            'without_inventory_count': max(0, active_endpoint_count - sum(1 for endpoint, _ in latest_rows if endpoint.is_active)),
            'stale_endpoint_count': sum(
                1 for endpoint, _ in latest_rows
                if endpoint.is_active and endpoints_by_id[str(endpoint.id)]['inventory_state'] == 'stale'
            ),
            'unknown_endpoint_count': sum(
                1 for endpoint, _ in latest_rows
                if endpoint.is_active and endpoints_by_id[str(endpoint.id)]['inventory_state'] == 'unknown'
            ),
            'updated_at': now.isoformat(),
        },
    })


@require_GET
@never_cache
def software_packages_data(request):
    if not _authorized(request):
        return JsonResponse({'error': 'forbidden'}, status=403)
    query = str(request.GET.get('q', '')).strip()
    packages = SoftwarePackageRecord.objects.all()
    if query:
        from django.db.models import Q
        packages = packages.filter(Q(friendly_name__icontains=query) | Q(original_filename__icontains=query))
    return JsonResponse({'items': [{
        'id': str(package.id),
        'friendly_name': package.friendly_name,
        'original_filename': package.original_filename,
        'package_type': package.package_type,
        'version': package.version or None,
        'size_bytes': package.size_bytes,
        'sha256': package.sha256 or None,
        'signature_status': package.signature_status,
        'signer': package.signer or None,
        'approval_status': package.approval_status,
        'approved_at': package.approved_at.isoformat() if package.approved_at else None,
        'approved_by': package.approved_by.get_username() if package.approved_by else None,
        'revoked_at': package.revoked_at.isoformat() if package.revoked_at else None,
        'revoked_by': package.revoked_by.get_username() if package.revoked_by else None,
        'created_at': package.created_at.isoformat(),
        'created_by': package.created_by.get_username() if package.created_by else None,
        'updated_at': package.updated_at.isoformat(),
    } for package in packages.select_related('approved_by', 'revoked_by', 'created_by')]})

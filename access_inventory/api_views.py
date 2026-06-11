from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from .agent_auth import authenticate_inventory_agent_token
from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AclEntry,
    FileServer,
    Folder,
    InventoryAgentRun,
    Share,
)
from .serializers import (
    ADGroupMembershipSerializer,
    ADGroupSerializer,
    ADOrganizationalUnitSerializer,
    ADUserSerializer,
    AclEntrySerializer,
    FileServerSerializer,
    FolderSerializer,
    ShareSerializer,
)


class ADOrganizationalUnitViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ADOrganizationalUnit.objects.all()
    serializer_class = ADOrganizationalUnitSerializer


class ADUserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ADUser.objects.select_related('ou').all()
    serializer_class = ADUserSerializer


class ADGroupViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ADGroup.objects.select_related('ou').all()
    serializer_class = ADGroupSerializer


class ADGroupMembershipViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ADGroupMembership.objects.select_related('parent_group', 'member_user', 'member_group').all()
    serializer_class = ADGroupMembershipSerializer


class FileServerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FileServer.objects.select_related('rmm_agent').all()
    serializer_class = FileServerSerializer


class ShareViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Share.objects.select_related('file_server').all()
    serializer_class = ShareSerializer


class FolderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Folder.objects.select_related('share', 'share__file_server').all()
    serializer_class = FolderSerializer


class AclEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AclEntry.objects.select_related('folder', 'ad_user', 'ad_group').all()
    serializer_class = AclEntrySerializer


def agent_response_summary(result):
    return {
        'created': result.created,
        'updated': result.updated,
        'ignored': result.ignored,
        'errors': result.errors_count,
        'details': result.as_dict()['details'],
    }


class InventoryAgentAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    run_type = None

    def authenticate_agent(self, request):
        token = request.headers.get('X-Nightowl-Agent-Token', '')
        return authenticate_inventory_agent_token(token)

    def create_run(self, agent):
        return InventoryAgentRun.objects.create(
            agent=agent,
            run_type=self.run_type,
            status=InventoryAgentRun.STATUS_RUNNING,
            started_at=timezone.now(),
        )

    def finish_run(self, run, result, message=''):
        run.status = (
            InventoryAgentRun.STATUS_PARTIAL_SUCCESS
            if result.errors_count
            else InventoryAgentRun.STATUS_SUCCESS
        )
        run.finished_at = timezone.now()
        run.items_created = result.created
        run.items_updated = result.updated
        run.items_ignored = result.ignored
        run.errors_count = result.errors_count
        run.message = message
        run.save(update_fields=[
            'status',
            'finished_at',
            'items_created',
            'items_updated',
            'items_ignored',
            'errors_count',
            'message',
            'updated_at',
        ])

    def fail_run(self, run, error):
        run.status = InventoryAgentRun.STATUS_FAILED
        run.finished_at = timezone.now()
        run.message = str(error)
        run.errors_count = 1
        run.save(update_fields=['status', 'finished_at', 'message', 'errors_count', 'updated_at'])


class InventoryAgentHeartbeatView(InventoryAgentAPIView):
    run_type = InventoryAgentRun.RUN_HEARTBEAT

    def post(self, request):
        agent = self.authenticate_agent(request)
        if agent is None:
            return Response({'detail': 'Invalid inventory agent token.'}, status=401)

        version = str(request.data.get('version') or '').strip()
        agent.last_seen_at = timezone.now()
        if version:
            agent.version = version
        agent.save(update_fields=['last_seen_at', 'version', 'updated_at'])

        run = InventoryAgentRun.objects.create(
            agent=agent,
            run_type=self.run_type,
            status=InventoryAgentRun.STATUS_SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            message='Heartbeat received.',
        )
        return Response({
            'status': 'ok',
            'agent_id': agent.id,
            'run_id': run.id,
        })


class InventoryAgentFileAclView(InventoryAgentAPIView):
    run_type = InventoryAgentRun.RUN_FILE_ACL

    def post(self, request):
        from .services.import_file_acl import import_file_acl_data

        agent = self.authenticate_agent(request)
        if agent is None:
            return Response({'detail': 'Invalid inventory agent token.'}, status=401)

        agent.last_seen_at = timezone.now()
        agent.save(update_fields=['last_seen_at', 'updated_at'])
        run = self.create_run(agent)

        try:
            result = import_file_acl_data(request.data)
            self.finish_run(run, result, message='File ACL payload imported.')
        except Exception as error:
            self.fail_run(run, error)
            return Response({'detail': 'File ACL import failed.', 'error': str(error)}, status=400)

        return Response({
            'status': 'ok',
            'run_id': run.id,
            'summary': agent_response_summary(result),
        })


class InventoryAgentAdInventoryView(InventoryAgentAPIView):
    run_type = InventoryAgentRun.RUN_AD_INVENTORY

    def post(self, request):
        from .services.import_ad_inventory import import_ad_inventory_data

        agent = self.authenticate_agent(request)
        if agent is None:
            return Response({'detail': 'Invalid inventory agent token.'}, status=401)

        agent.last_seen_at = timezone.now()
        agent.save(update_fields=['last_seen_at', 'updated_at'])
        run = self.create_run(agent)

        try:
            result = import_ad_inventory_data(request.data)
            self.finish_run(run, result, message='AD inventory payload imported.')
        except Exception as error:
            self.fail_run(run, error)
            return Response({'detail': 'AD inventory import failed.', 'error': str(error)}, status=400)

        return Response({
            'status': 'ok',
            'run_id': run.id,
            'summary': agent_response_summary(result),
        })

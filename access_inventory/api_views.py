from rest_framework import viewsets

from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AclEntry,
    FileServer,
    Folder,
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

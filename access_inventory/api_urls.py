from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    ADGroupMembershipViewSet,
    ADGroupViewSet,
    ADOrganizationalUnitViewSet,
    ADUserViewSet,
    AclEntryViewSet,
    FileServerViewSet,
    FolderViewSet,
    InventoryAgentAdInventoryView,
    InventoryAgentFileAclView,
    InventoryAgentHeartbeatView,
    ShareViewSet,
)


router = DefaultRouter()
router.register('ous', ADOrganizationalUnitViewSet)
router.register('users', ADUserViewSet)
router.register('groups', ADGroupViewSet)
router.register('memberships', ADGroupMembershipViewSet)
router.register('file-servers', FileServerViewSet)
router.register('shares', ShareViewSet)
router.register('folders', FolderViewSet)
router.register('acl-entries', AclEntryViewSet)

urlpatterns = [
    path('agent/heartbeat/', InventoryAgentHeartbeatView.as_view(), name='access-inventory-agent-heartbeat'),
    path('agent/file-acl/', InventoryAgentFileAclView.as_view(), name='access-inventory-agent-file-acl'),
    path('agent/ad-inventory/', InventoryAgentAdInventoryView.as_view(), name='access-inventory-agent-ad-inventory'),
    path('', include(router.urls)),
]

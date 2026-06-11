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
    path('', include(router.urls)),
]

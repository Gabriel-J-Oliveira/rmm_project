from django.urls import path

from .views import (
    AgentEnrollView,
    AgentDeploymentBootstrapScriptView,
    AgentDeploymentCompleteView,
    AgentDeploymentMetadataView,
    AgentSelfUninstallAuthorizeView,
    AgentSelfUninstallConsumeView,
    AgentHeartbeatView,
    AgentInventoryCollectionView,
    AgentJobsPullView,
    AgentJobsResultView,
    AgentStatusView,
    AgentUpdatePolicyView,
)

urlpatterns = [
    path('deployments/bootstrap.ps1', AgentDeploymentBootstrapScriptView.as_view(), name='agent-deployment-bootstrap'),
    path('deployments/metadata/', AgentDeploymentMetadataView.as_view(), name='agent-deployment-metadata'),
    path('deployments/complete/', AgentDeploymentCompleteView.as_view(), name='agent-deployment-complete'),
    path('self-uninstall/authorize/', AgentSelfUninstallAuthorizeView.as_view(), name='agent-self-uninstall-authorize'),
    path('self-uninstall/consume/', AgentSelfUninstallConsumeView.as_view(), name='agent-self-uninstall-consume'),
    path('enroll/', AgentEnrollView.as_view(), name='agent-enroll'),
    path('heartbeat/', AgentHeartbeatView.as_view(), name='agent-heartbeat'),
    path('status/', AgentStatusView.as_view(), name='agent-status'),
    path('update-policy/', AgentUpdatePolicyView.as_view(), name='agent-update-policy'),
    path('inventory/', AgentInventoryCollectionView.as_view(collection_type='full_inventory'), name='agent-inventory'),
    path('inventory/system/', AgentInventoryCollectionView.as_view(collection_type='system'), name='agent-inventory-system'),
    path('inventory/hardware/', AgentInventoryCollectionView.as_view(collection_type='hardware'), name='agent-inventory-hardware'),
    path('inventory/network/', AgentInventoryCollectionView.as_view(collection_type='network'), name='agent-inventory-network'),
    path('inventory/disks/', AgentInventoryCollectionView.as_view(collection_type='disk'), name='agent-inventory-disks'),
    path('inventory/security/', AgentInventoryCollectionView.as_view(collection_type='security'), name='agent-inventory-security'),
    path('inventory/software/', AgentInventoryCollectionView.as_view(collection_type='software'), name='agent-inventory-software'),
    path('inventory/patches/', AgentInventoryCollectionView.as_view(collection_type='patches'), name='agent-inventory-patches'),
    path('collect/', AgentInventoryCollectionView.as_view(collection_type='full_inventory'), name='agent-collect'),
    path('jobs/pull/', AgentJobsPullView.as_view(), name='agent-jobs-pull'),
    path('jobs/result/', AgentJobsResultView.as_view(), name='agent-jobs-result'),
]

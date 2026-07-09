from django.urls import path

from .views import AgentEnrollView, AgentHeartbeatView, AgentInventoryCollectionView, AgentJobsPullView, AgentJobsResultView

urlpatterns = [
    path('enroll/', AgentEnrollView.as_view(), name='agent-enroll'),
    path('heartbeat/', AgentHeartbeatView.as_view(), name='agent-heartbeat'),
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

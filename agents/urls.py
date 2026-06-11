from django.urls import path

from .views import AgentEnrollView, AgentHeartbeatView

urlpatterns = [
    path('enroll/', AgentEnrollView.as_view(), name='agent-enroll'),
    path('heartbeat/', AgentHeartbeatView.as_view(), name='agent-heartbeat'),
]

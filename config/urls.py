"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from dashboard import views as dashboard_views
from config import views as config_views
from tickets import views as ticket_views

urlpatterns = [
    path('', dashboard_views.index, name='home'),
    path('admin/', admin.site.urls),
    path('accounts/login/', config_views.NightOwlLoginView.as_view(), name='login'),
    path('accounts/logout/', config_views.nightowl_logout, name='logout'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('api/agent/', include('agents.urls')),
    path('api/endpoints/<str:pk>/', dashboard_views.endpoint_detail_data, name='api-endpoint-detail'),
    path('api/endpoints/<str:pk>/jobs/', dashboard_views.endpoint_job_create, name='api-endpoint-job-create'),
    path('api/endpoints/<str:pk>/update-policy/', dashboard_views.endpoint_update_policy_update, name='api-endpoint-update-policy'),
    path('api/access-inventory/', include('access_inventory.api_urls')),
    path('access-inventory/', include('access_inventory.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('tickets/', include('tickets.urls')),
    path('meus-chamados/', ticket_views.ticket_requester_list, name='requester-ticket-list'),
    path('meus-chamados/abrir/', ticket_views.ticket_requester_create, name='requester-ticket-create'),
    path('meus-chamados/<int:number>/', ticket_views.ticket_requester_detail, name='requester-ticket-detail'),
    path('meus-chamados/<int:number>/responder/', ticket_views.ticket_requester_comment, name='requester-ticket-comment'),
    path('meus-chamados/<int:number>/reabrir/', ticket_views.ticket_requester_reopen, name='requester-ticket-reopen'),
    path('portal/chamados/', ticket_views.ticket_portal_list, name='ticket-portal-list'),
    path('portal/chamados/<int:number>/', ticket_views.ticket_portal_detail, name='ticket-portal-detail'),
    path('portal/chamados/<int:number>/comment/', ticket_views.ticket_portal_comment, name='ticket-portal-comment'),
    path('portal/chamados/<int:number>/reopen/', ticket_views.ticket_portal_reopen, name='ticket-portal-reopen'),
    path('noc/', dashboard_views.noc_view, name='noc'),
    path('alerts/', dashboard_views.alerts_list, name='alerts-list'),
    path('events/', dashboard_views.events_list, name='events-list'),
    path('jobs/', dashboard_views.jobs_list, name='jobs-list'),
    path('maintenance/', dashboard_views.maintenance_list, name='maintenance-list'),
    path('maintenance/email-outbox/', dashboard_views.email_outbox_list, name='email-outbox-list'),
    path('maintenance/email-outbox/process/', dashboard_views.email_outbox_process, name='email-outbox-process'),
    path('maintenance/email-outbox/retry-failed/', dashboard_views.email_outbox_retry_all, name='email-outbox-retry-all'),
    path('maintenance/email-outbox/<uuid:pk>/retry/', dashboard_views.email_outbox_retry, name='email-outbox-retry'),
    path('maintenance/email-outbox/<uuid:pk>/cancel/', dashboard_views.email_outbox_cancel, name='email-outbox-cancel'),
    path('maintenance/email-outbox/<uuid:pk>/pending/', dashboard_views.email_outbox_pending, name='email-outbox-pending'),
    path('agent-install/', dashboard_views.agent_install, name='agent-install'),
    path('agents/download/', dashboard_views.agent_install, name='agent-download'),
    path('agent-releases/', dashboard_views.agent_releases, name='agent-releases'),
    path('agent-releases/<uuid:pk>/action/', dashboard_views.agent_release_action, name='agent-release-action'),
    path('agent-install/enrollment/<uuid:pk>/revoke/', dashboard_views.agent_enrollment_revoke, name='agent-enrollment-revoke'),
    path('software/', dashboard_views.software_inventory, name='software-inventory'),
    path('software-policies/', dashboard_views.software_policies, name='software-policies'),
    path('software-policies/create/', dashboard_views.software_policy_create, name='software-policy-create'),
    path('software-policies/<uuid:pk>/update/', dashboard_views.software_policy_update, name='software-policy-update'),
    path('software-policies/<uuid:pk>/toggle-active/', dashboard_views.software_policy_toggle_active, name='software-policy-toggle-active'),
    path('software-policies/<uuid:pk>/delete/', dashboard_views.software_policy_delete, name='software-policy-delete'),
    path('software-policies/<uuid:pk>/exceptions/add/', dashboard_views.software_policy_exception_add, name='software-policy-exception-add'),
    path('software-policies/exceptions/<uuid:pk>/remove/', dashboard_views.software_policy_exception_remove, name='software-policy-exception-remove'),
    path('software/detail/', dashboard_views.software_detail, name='software-detail'),
    path('software/export/', dashboard_views.software_export, name='software-export'),
    path('alerts/<uuid:pk>/acknowledge/', dashboard_views.alert_acknowledge, name='alert-acknowledge'),
    path('alerts/<uuid:pk>/resolve/', dashboard_views.alert_resolve, name='alert-resolve'),
    path('alerts/<uuid:pk>/mute/', dashboard_views.alert_mute, name='alert-mute'),
    path('alerts/<uuid:pk>/comment/', dashboard_views.alert_comment, name='alert-comment'),
    path('endpoints/', dashboard_views.endpoint_list, name='endpoint-list'),
    path('endpoints/<str:pk>/', dashboard_views.endpoint_detail, name='endpoint-detail'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

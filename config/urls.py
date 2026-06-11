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

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/agent/', include('agents.urls')),
    path('api/access-inventory/', include('access_inventory.api_urls')),
    path('access-inventory/', include('access_inventory.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('tickets/', include('tickets.urls')),
    path('noc/', dashboard_views.noc_view, name='noc'),
    path('alerts/', dashboard_views.alerts_list, name='alerts-list'),
    path('events/', dashboard_views.events_list, name='events-list'),
    path('maintenance/', dashboard_views.maintenance_list, name='maintenance-list'),
    path('agent-install/', dashboard_views.agent_install, name='agent-install'),
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
    path('endpoints/<uuid:pk>/', dashboard_views.endpoint_detail, name='endpoint-detail'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

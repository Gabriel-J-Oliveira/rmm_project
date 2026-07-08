from django.urls import path

from . import views


app_name = 'tickets'

urlpatterns = [
    path('', views.ticket_central, name='index'),
    path('central/', views.ticket_central, name='central'),
    path('my/', views.ticket_my, name='my'),
    path('new/', views.ticket_create, name='create'),
    path('dashboard/', views.ticket_dashboard, name='dashboard'),
    path('categories/', views.ticket_categories, name='categories'),
    path('automation/', views.ticket_automation_rules, name='automation'),
    path('settings/', views.ticket_settings, name='settings'),
    path('portal/', views.ticket_portal_list, name='portal-list'),
    path('portal/<int:number>/', views.ticket_portal_detail, name='portal-detail'),
    path('portal/<int:number>/comment/', views.ticket_portal_comment, name='portal-comment'),
    path('portal/<int:number>/reopen/', views.ticket_portal_reopen, name='portal-reopen'),
    path('settings/api/config/', views.ticket_settings_api, name='settings-api'),
    path('api/tickets/', views.ticket_api_create, name='api-create'),
    path('fake/<str:action>/', views.ticket_fake_action, name='fake-action-root'),
    path('<int:number>/api/update/', views.ticket_api_update, name='api-update'),
    path('<int:number>/api/comments/', views.ticket_api_comment, name='api-comment'),
    path('<int:number>/api/public-conversation/', views.ticket_api_public_conversation, name='api-public-conversation'),
    path('<int:number>/api/attachments/', views.ticket_api_attachment, name='api-attachment'),
    path('<int:number>/api/actions/', views.ticket_api_action, name='api-action'),
    path('attachments/<uuid:attachment_id>/', views.ticket_attachment_download, name='attachment-download'),
    path('<int:number>/', views.ticket_detail, name='detail'),
    path('<int:number>/fake/<str:action>/', views.ticket_fake_action, name='fake-action'),
]

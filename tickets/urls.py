from django.urls import path

from . import views


app_name = 'tickets'

urlpatterns = [
    path('', views.ticket_list, name='list'),
    path('queue/', views.ticket_queue, name='queue'),
    path('my/', views.ticket_my, name='my'),
    path('new/', views.ticket_create, name='create'),
    path('dashboard/', views.ticket_dashboard, name='dashboard'),
    path('painel/', views.ticket_service_panel, name='service-panel'),
    path('categories/', views.ticket_categories, name='categories'),
    path('settings/', views.ticket_settings, name='settings'),
    path('fake/<str:action>/', views.ticket_fake_action, name='fake-action-root'),
    path('<int:number>/', views.ticket_detail, name='detail'),
    path('<int:number>/fake/<str:action>/', views.ticket_fake_action, name='fake-action'),
]

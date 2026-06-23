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
    path('settings/', views.ticket_settings, name='settings'),
    path('fake/<str:action>/', views.ticket_fake_action, name='fake-action-root'),
    path('<int:number>/', views.ticket_detail, name='detail'),
    path('<int:number>/fake/<str:action>/', views.ticket_fake_action, name='fake-action'),
]

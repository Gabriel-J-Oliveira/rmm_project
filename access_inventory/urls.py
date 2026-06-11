from django.urls import path

from . import views


app_name = 'access_inventory'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('users/', views.user_list, name='user-list'),
    path('users/<int:pk>/', views.user_detail, name='user-detail'),
    path('groups/', views.group_list, name='group-list'),
    path('groups/<int:pk>/', views.group_detail, name='group-detail'),
    path('file-servers/', views.file_server_list, name='file-server-list'),
    path('file-servers/<int:pk>/', views.file_server_detail, name='file-server-detail'),
    path('folders/<int:pk>/', views.folder_detail, name='folder-detail'),
]

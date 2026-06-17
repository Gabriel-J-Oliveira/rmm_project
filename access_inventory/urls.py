from django.urls import path

from . import views


app_name = 'access_inventory'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('explorer/', views.explorer, name='explorer'),
    path('folders/', views.folder_list, name='folder-list'),
    path('folders/<int:pk>/', views.folder_detail, name='folder-detail'),
    path('users/', views.user_list, name='user-list'),
    path('users/<int:pk>/', views.user_detail, name='user-detail'),
    path('groups/', views.group_list, name='group-list'),
    path('groups/<int:pk>/', views.group_detail, name='group-detail'),
    path('ous/', views.ou_list, name='ou-list'),
    path('ous/<int:pk>/', views.ou_detail, name='ou-detail'),
    path('file-servers/', views.file_server_list, name='file-server-list'),
    path('file-servers/<int:pk>/', views.file_server_detail, name='file-server-detail'),
    path('unknown-identities/', views.unknown_identities, name='unknown-identities'),
    path('reviews/', views.review_plan_list, name='review-plan-list'),
    path('reviews/<int:plan_id>/', views.review_plan_detail, name='review-plan-detail'),
    path('reviews/<int:plan_id>/folders/<int:folder_id>/', views.review_folder_detail, name='review-folder-detail'),
]

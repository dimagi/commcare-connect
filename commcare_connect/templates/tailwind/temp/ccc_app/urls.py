from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),        # Root URL for home
    path('dashboard/', views.dashboard, name='dashboard'),
    path('worker/', views.worker, name='worker'), #
    path('opportunities/', views.opportunities, name='opportunities'), #
    path('tables/', views.table_view, name='tables'),
    path('visits/', views.opportunity_visits, name='visits'), 
    path('create/', views.create_opportunity, name='create_opportunity'), #
]
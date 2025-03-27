# ccc_app/context_processors.py
from django.urls import resolve

def header_title(request):
    # Map URL names to titles
    title_map = {
        'home': 'Home',
        'about': 'About',
        'dashboard': 'Dashboard',
        # Add more mappings as needed
    }
    current_url_name = resolve(request.path_info).url_name
    return {'header_title': title_map.get(current_url_name, 'CommCareConnect')}
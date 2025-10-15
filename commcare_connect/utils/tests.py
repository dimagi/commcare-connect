from django.contrib.auth.models import Permission
from django.test import Client


def check_basic_permissions(user, url, permission_codename):
    client = Client()

    # Anonymous → redirect
    response = client.get(url)
    assert response.status_code == 302
    assert "/accounts/login/" in response.url

    # Logged-in without permission → forbidden
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 403
    client.logout()

    # With permission → allowed
    perm = Permission.objects.get(codename=permission_codename)
    user.user_permissions.add(perm)

    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    client.logout()

    # Superuser → allowed
    user.user_permissions.remove(perm)
    user.is_superuser = True
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    client.logout()

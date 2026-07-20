from django.urls import reverse


def test_swagger_ui_accessible_by_normal_user(client):
    url = reverse("api-docs")
    response = client.get(url)
    assert response.status_code == 200


def test_api_schema_generated_successfully(admin_client):
    url = reverse("api-schema")
    response = admin_client.get(url)
    assert response.status_code == 200

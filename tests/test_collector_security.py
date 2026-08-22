"""collector/main.py — kimlik doğrulaması istemeyen uçların sızdırdıkları.

Bu dosyadaki testler geçen taramada kapatılan iki açığı korur: belge uçları
kapalı kalmalı ve kimliksiz uçlar collector sürümünü söylememeli.
"""

from fastapi.testclient import TestClient

from main import app
from version import COLLECTOR_VERSION

# TestClient gerçek bir sunucu ayağa kaldırmaz; isteği doğrudan uygulamaya verir.
# `with` bloğu kullanılmadığı için lifespan çalışmaz, yani Supabase bağlantısı
# hiç kurulmaz — bu testlerin ağa ve secret'a ihtiyacı yok.
client = TestClient(app)


def test_docs_endpoint_is_closed():
    """Swagger arayüzü kapalı olmalı."""
    assert client.get("/docs").status_code == 404


def test_redoc_endpoint_is_closed():
    """ReDoc arayüzü de kapalı olmalı — docs_url tek başına yetmez."""
    assert client.get("/redoc").status_code == 404


def test_openapi_schema_is_closed():
    """Asıl içerik burada: tam API sözleşmesi kimliksiz servis edilmemeli."""
    assert client.get("/openapi.json").status_code == 404


def test_root_does_not_leak_version():
    """GET / servis adını verir, sürümü vermez."""
    response = client.get("/")
    assert response.status_code == 200
    assert COLLECTOR_VERSION not in response.text


def test_health_does_not_leak_version():
    """Fly.io'nun yokladığı uç da sürüm sızdırmamalı."""
    response = client.get("/health")
    assert response.status_code == 200
    assert COLLECTOR_VERSION not in response.text

"""
Supabase'e yazma katmanı — PostgREST üzerinden, service key ile.

Service key RLS'i bypass eder; bu yüzden `account_id` ve `device_id` filtreleri
bu modülü çağıran kodun sorumluluğundadır (CLAUDE.md §11, Boşluk D).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("tracebox.supabase")

SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_SERVICE_KEY_ENV = "SUPABASE_SERVICE_KEY"

# PostgREST'in taban yolu. Kod bunu kendisi eklediği için `SUPABASE_URL`
# yalnızca proje adresini taşımalıdır.
REST_PATH = "/rest/v1"

# Fly makinesi ile Supabase aynı coğrafyada (fra / eu-central-1); bu süre normal
# bir yazma için fazlasıyla yeterlidir. Aşılırsa agent spool'unda veri durur ve
# bir sonraki turda tekrar denenir.
REQUEST_TIMEOUT_SECONDS = 10.0

# PostgREST'in çakışan satırları sessizce atlaması için gereken başlık.
# `id` birincil anahtar olduğundan ON CONFLICT (id) DO NOTHING ile aynı sonucu
# verir (CLAUDE.md §11, Boşluk C).
PREFER_IGNORE_DUPLICATES = "resolution=ignore-duplicates,return=minimal"

# Yanıt gövdesi istenmediğinde kullanılır — güncelleme sonrası satırı geri
# okumaya gerek yok.
PREFER_MINIMAL = "return=minimal"


class SupabaseError(RuntimeError):
    """Supabase'e yazma/okuma başarısız oldu."""


class SupabaseClient:
    """PostgREST istemcisi. Uygulama ömrü boyunca tek örnek yaşar."""

    def __init__(self, url: str, service_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"{url}{REST_PATH}",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def find_device_by_key_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Anahtar hash'ine karşılık gelen cihaz satırını döndürür, yoksa None."""
        response = await self._request(
            "GET",
            "/devices",
            params={
                "key_hash": f"eq.{key_hash}",
                "select": "id,account_id,device_name,key_hash,logging_enabled,pending_delete",
                "limit": "1",
            },
        )
        rows = response.json()
        return rows[0] if rows else None

    async def update_device(self, device_id: str, fields: dict[str, Any]) -> None:
        """Cihaz satırının verilen sütunlarının üzerine yazar."""
        await self._request(
            "PATCH",
            "/devices",
            params={"id": f"eq.{device_id}"},
            json=fields,
            headers={"Prefer": PREFER_MINIMAL},
        )

    async def insert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Satırları ekler; `id` çakışanları sessizce atlar."""
        if not rows:
            return

        await self._request(
            "POST",
            f"/{table}",
            json=rows,
            headers={"Prefer": PREFER_IGNORE_DUPLICATES},
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """İstek atar; ağ hatasını ve 4xx/5xx yanıtını SupabaseError'a çevirir."""
        # Hata metni `fly logs` çıktısına düşer; dışarıya dönen yanıt genel
        # kalır (auth.py / endpoints_ingest.py 503'e çevirir).
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            logger.error("Supabase %s %s ulaşılamadı: %r", method, path, error)
            raise SupabaseError(f"{method} {path}: {error}") from error

        if response.is_error:
            logger.error(
                "Supabase %s %s → %s: %s", method, path, response.status_code, response.text[:300]
            )
            raise SupabaseError(f"{method} {path}: {response.status_code}")

        return response


_client: SupabaseClient | None = None


def _normalize_url(raw: str) -> str:
    """Proje adresini sondaki `/` ve `/rest/v1` ekinden arındırır.

    Değişkene REST adresinin tamamı girilirse yol iki kez eklenir ve Supabase
    404 (PGRST125) döner.
    """
    url = raw.strip().rstrip("/")
    if url.endswith(REST_PATH):
        url = url[: -len(REST_PATH)]

    return url


def init_client() -> SupabaseClient:
    """İstemciyi ortam değişkenlerinden kurar (uygulama açılışında bir kez).

    Değişkenler eksikse burada hata verilir: süreç ayağa kalkmaz, Fly sağlık
    kontrolünde çakılır ve deploy bir önceki sürümde kalır.
    """
    global _client

    url = _normalize_url(os.environ.get(SUPABASE_URL_ENV, ""))
    service_key = os.environ.get(SUPABASE_SERVICE_KEY_ENV, "").strip()
    missing = [
        name
        for name, value in ((SUPABASE_URL_ENV, url), (SUPABASE_SERVICE_KEY_ENV, service_key))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Eksik ortam değişkeni: {', '.join(missing)}")

    # Yalnızca adres ve anahtarın ön eki loglanır — anahtarın kendisi asla.
    logger.info("Supabase hedefi: %s (anahtar: %s…)", url, service_key[:11])

    _client = SupabaseClient(url, service_key)
    return _client


async def close_client() -> None:
    """Açık bağlantıları kapatır (uygulama kapanışında)."""
    global _client

    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> SupabaseClient:
    """Kurulmuş istemciyi döndürür — endpoint'ler bunu kullanır."""
    if _client is None:
        raise RuntimeError("Supabase istemcisi kurulmadı.")

    return _client

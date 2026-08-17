"""
Kimlik doğrulama — cihaz anahtarı modu.

Cihaz kimliği payload'dan değil ANAHTARDAN türetilir: collector `sha256(key)`
hesaplar, `devices.key_hash` ile eşleştirir ve satırdan `device_id` +
`account_id` alır (CLAUDE.md §11, Boşluk A).

User JWT modu (`POST /devices`) M5'te bu modüle eklenecek.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from hashing import hash_device_key, hashes_match
from supabase_client import SupabaseError, get_client

BEARER_PREFIX = "Bearer "

# Tüm başarısız doğrulamalar aynı yanıtı verir: anahtarın var olup olmadığı,
# biçiminin doğru olup olmadığı dışarıya sızmaz.
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Geçersiz cihaz anahtarı.",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class DeviceIdentity:
    """Doğrulanmış cihaz — satırlara bu iki alan sunucu tarafında eklenir."""

    id: str
    account_id: str
    device_name: str
    pending_delete: bool


async def require_device(
    authorization: Annotated[str | None, Header()] = None,
) -> DeviceIdentity:
    """`Authorization: Bearer <device_key>` başlığını doğrular.

    Eşleşme yoksa 401. Supabase'e ulaşılamıyorsa 503: bu bir yetki sorunu
    değildir ve agent'ın anahtarını geçersiz sayıp vazgeçmesi istenmez.
    """
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        raise _UNAUTHORIZED

    key = authorization[len(BEARER_PREFIX) :].strip()
    if not key:
        raise _UNAUTHORIZED

    key_hash = hash_device_key(key)
    try:
        row = await get_client().find_device_by_key_hash(key_hash)
    except SupabaseError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Doğrulama şu an yapılamıyor.",
        ) from error

    # Satır sorgusu zaten hash eşitliğiyle yapıldı; karşılaştırma sabit süreli
    # bir ikinci kapı olarak burada tekrarlanır.
    if row is None or not hashes_match(row["key_hash"], key_hash):
        raise _UNAUTHORIZED

    return DeviceIdentity(
        id=row["id"],
        account_id=row["account_id"],
        device_name=row["device_name"],
        pending_delete=row["pending_delete"],
    )


# Endpoint imzalarında tekrar etmemek için hazır bağımlılık tipi.
AuthenticatedDevice = Annotated[DeviceIdentity, Depends(require_device)]

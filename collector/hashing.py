"""
Cihaz anahtarı hash'leme ve karşılaştırma.

Anahtarın düz hali hiçbir yerde saklanmaz; `devices.key_hash` yalnızca burada
üretilen hash'i tutar (CLAUDE.md §6).
"""

from __future__ import annotations

import hashlib
import hmac

# Anahtar ön eki. Doğrulamada kullanılmaz — yalnızca kullanıcının elindeki
# metnin ne olduğunu tanımasına yarar (CLAUDE.md §6).
KEY_PREFIX = "tbx_live_"


def hash_device_key(key: str) -> str:
    """Anahtarın UTF-8 baytlarının SHA-256'sını küçük harf hex olarak döndürür.

    Tanım agent kurulumunda ve `devices.key_hash` satırında birebir aynıdır;
    kayması halinde her istek 401 döner (md/memory/runbook.md §5.1).
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def hashes_match(left: str, right: str) -> bool:
    """İki hash'i sabit sürede (constant-time) karşılaştırır.

    `==` karşılaştırması ilk farklı baytta durur ve süre farkı üzerinden bilgi
    sızdırabilir; `compare_digest` uzunluk aynı olduğu sürece bunu yapmaz.
    """
    return hmac.compare_digest(left, right)

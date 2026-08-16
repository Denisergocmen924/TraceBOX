"""
Zaman damgası yardımcıları.

Agent iki farklı saat kullanır ve bunlar karıştırılmamalıdır:

  * time.monotonic() — "ne kadar zaman geçti" sorusunu cevaplar. Yalnızca ileri
    gider, sistem saati değişse bile etkilenmez. Döngü sayaçları bunu kullanır
    (loop.py).
  * wall-clock UTC — "hangi an" sorusunu cevaplar. Sunucuya giden her
    measured_at damgası bu modülden çıkar.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Damgalar saniye çözünürlüğünde tutulur: en sık ölçüm aralığı saniyelerle
# ifade ediliyor, mikrosaniye ne payload'da ne grafikte bir şey değiştiriyor.
_TIMESPEC = "seconds"


def utc_now_iso() -> str:
    """Şu anı ISO 8601 UTC olarak döndürür (örn. 2026-08-16T15:02:28+00:00)."""
    return datetime.now(timezone.utc).isoformat(timespec=_TIMESPEC)


def epoch_to_utc_iso(epoch_seconds: float) -> str:
    """Unix zaman damgasını ISO 8601 UTC'ye çevirir.

    psutil.boot_time() gibi epoch döndüren kaynaklar için; yerel saat dilimi
    hesaba katılmaz, değer doğrudan UTC olarak yorumlanır.
    """
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat(timespec=_TIMESPEC)

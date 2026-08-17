"""
Spool — gönderilmeyi bekleyen kayıtların disk üzerindeki sırası.

Toplanan her kayıt önce buraya yazılır, ancak collector'dan 200 alındıktan
sonra silinir (CLAUDE.md §7). Ağ kesilse, collector kapalı olsa ya da agent
yeniden başlasa da veri kaybolmaz.

Kapasite bir halka tampondur (ring buffer): yaş ve boyut sınırı aşıldığında en
eski kayıtlar düşer. Sınırlar config'ten gelir (`spool_max_age_days`,
`spool_max_size_mb`).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SPOOL_DIRNAME = "spool"
SPOOL_FILENAME = "spool.db"

# Tür etiketi (CLAUDE.md §7). Bugün üç türün ortak bir bütçesi var; etiket
# sayesinde tür başına bütçeye geçiş tek kural değişikliğidir.
RECORD_METRIC = "metric"
RECORD_LOG = "log"
RECORD_CRASH = "crash"

SECONDS_PER_DAY = 86400
BYTES_PER_MB = 1024 * 1024

# Boyut sınırı aşıldığında tek seferde silinen kayıt sayısı. Satır satır
# silmek her adımda dosya boyutunu yeniden ölçmek demek olurdu.
TRIM_CHUNK_ROWS = 200

_SCHEMA = """
create table if not exists pending (
    uuid         text primary key,
    type         text not null,
    payload_json text not null,
    created_at   real not null
);
create index if not exists pending_created_at on pending (created_at);
"""


@dataclass(frozen=True)
class SpooledRecord:
    """Spool'dan okunan bir kayıt — payload doğrudan wire gövdesine girer."""

    uuid: str
    type: str
    payload: dict


class Spool:
    """SQLite tabanlı bekleme alanı.

    Tek süreç ve tek thread tarafından kullanılır; eşzamanlı erişim koruması
    state.json'daki kilidin kapsamındadır (state.py → SingleWriterLock).
    """

    def __init__(self, directory: Path, *, max_age_days: int, max_size_mb: int) -> None:
        self._path = directory / SPOOL_DIRNAME / SPOOL_FILENAME
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_age_seconds = max_age_days * SECONDS_PER_DAY
        self._max_size_bytes = max_size_mb * BYTES_PER_MB

        self._connection = sqlite3.connect(self._path, isolation_level=None)
        self._configure()

    @property
    def path(self) -> Path:
        return self._path

    def add(self, record_type: str, payload: dict) -> None:
        """Kaydı sıraya ekler ve kapasite sınırlarını uygular.

        payload, collector'ın beklediği gövdenin aynısıdır; gönderim anında
        yeniden biçimlendirilmez.
        """
        self._connection.execute(
            "insert or ignore into pending (uuid, type, payload_json, created_at) "
            "values (?, ?, ?, ?)",
            (payload["uuid"], record_type, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        self._enforce_limits()

    def take(self, limit: int) -> list[SpooledRecord]:
        """En eski kayıtlardan başlayarak en fazla `limit` tane döndürür.

        Sıralama rowid iledir: kayıtların eklenme sırası, sistem saati geri
        alınsa bile bozulmaz.
        """
        rows = self._connection.execute(
            "select uuid, type, payload_json from pending order by rowid limit ?",
            (limit,),
        ).fetchall()

        return [SpooledRecord(uuid, type_, json.loads(payload)) for uuid, type_, payload in rows]

    def ack(self, uuids: list[str]) -> None:
        """Gönderimi onaylanan kayıtları siler — yalnızca 200 sonrası çağrılır."""
        if not uuids:
            return

        self._connection.executemany(
            "delete from pending where uuid = ?", [(value,) for value in uuids]
        )

    def count(self) -> int:
        """Bekleyen kayıt sayısı."""
        return self._connection.execute("select count(*) from pending").fetchone()[0]

    def size_bytes(self) -> int:
        """Verinin kapladığı alan: sayfa sayısı × sayfa boyutu.

        Dosyanın diskteki boyutu ölçülmez. WAL modunda silinen kayıtların yeri
        ancak checkpoint'te ana dosyadan düşer; bu arada dosya boyutuna bakan
        bir bütçe, silmenin işe yaramadığını sanıp spool'u tamamen boşaltır.
        Sayfa sayısı ise silme commit edilir edilmez küçülür (auto_vacuum=FULL).
        """
        page_count = self._connection.execute("pragma page_count").fetchone()[0]
        page_size = self._connection.execute("pragma page_size").fetchone()[0]
        return page_count * page_size

    def close(self) -> None:
        self._connection.close()

    def _configure(self) -> None:
        """Bağlantı ayarları ve şema.

        auto_vacuum=FULL tablo oluşturulmadan önce verilir; silinen kayıtların
        yeri işletim sistemine geri döner, yoksa dosya küçülmez ve boyut sınırı
        anlamını yitirir.
        """
        self._connection.execute("pragma auto_vacuum=FULL")
        self._connection.execute("pragma journal_mode=WAL")
        # Her commit diske inene kadar beklenir: çöküş anına kadar toplanan
        # veriyi korumak bu modülün varlık sebebidir.
        self._connection.execute("pragma synchronous=FULL")
        self._connection.executescript(_SCHEMA)

    def _enforce_limits(self) -> None:
        """Yaş ve boyut sınırlarını uygular — sınırı aşan en eski kayıtlar düşer."""
        cutoff = time.time() - self._max_age_seconds
        trimmed = self._connection.execute(
            "delete from pending where created_at < ?", (cutoff,)
        ).rowcount

        while self.size_bytes() > self._max_size_bytes:
            deleted = self._connection.execute(
                "delete from pending where rowid in "
                "(select rowid from pending order by rowid limit ?)",
                (TRIM_CHUNK_ROWS,),
            ).rowcount
            if deleted == 0:
                break
            trimmed += deleted

        # Boşalan yer ana dosyaya ancak checkpoint'te geri döner; yoksa WAL
        # büyümeye devam eder ve diskteki toplam ayak izi bütçeyi aşar.
        if trimmed > 0:
            self._connection.execute("pragma wal_checkpoint(TRUNCATE)")

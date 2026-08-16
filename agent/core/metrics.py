"""
Ölçüm toplama — psutil ile CPU, RAM, disk ve ağ.

Çekirdek metrikler (CLAUDE.md §4.2): cpu_percent, ram_used_mb, disk_percent,
net_sent_mb, net_recv_mb. Eklenti alanları (sıcaklık, swap, load average, GPU)
M7'de bu modüle eklenecek; şu an toplanmıyor.

M2 KAPSAMI: ölçüm gerçek, gönderim yok. Örnekler yalnızca ekrana basılır.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import psutil

from agent.core.clock import utc_now_iso

# Bayt -> MB çevrimi ikili tabanda (MiB). Şema sütunları "mb" adını taşır ama
# RAM ve disk değerleri işletim sisteminin raporladığı ikili birimdir; tek bir
# çevrim sabiti kullanmak RAM ve ağ sayılarının aynı ölçekte kalmasını sağlar.
BYTES_PER_MB = 1024 * 1024

# Doluluk oranının okunduğu bağlama noktası. Envanterdeki disk_total_mb de
# buradan gelir, böylece yüzde ile toplam aynı diski anlatır.
DISK_MOUNT_POINT = "/"


@dataclass(frozen=True)
class MetricSample:
    """Tek bir ölçüm anı — metrics tablosundaki bir satıra karşılık gelir.

    uuid agent tarafından üretilir: aynı örnek ağ hatası yüzünden iki kez
    gönderilse bile sunucu ON CONFLICT (id) DO NOTHING ile tekrarı eler
    (CLAUDE.md §11, Boşluk C).
    """

    uuid: str
    measured_at: str
    cpu_percent: float | None
    ram_used_mb: int
    disk_percent: float
    net_sent_mb: float | None
    net_recv_mb: float | None


class MetricsCollector:
    """Ardışık ölçümler arasında fark gerektiren alanların durumunu tutar.

    CPU yüzdesi ve ağ hızı MUTLAK değerler değildir; ikisi de "önceki ölçümden
    bu yana" hesaplanır. Bu yüzden toplayıcı bir nesnedir: önceki sayaçları
    bellekte taşır. Sayaçlar bilerek state.json'a yazılmaz — agent saatlerce
    kapalı kaldıysa aradaki farkı "hız" diye kaydetmek anlamsız bir ortalama
    üretirdi (karar: md/memory/decisions.md → "Ağ metriği birimi").
    """

    def __init__(self, disk_mount_point: str = DISK_MOUNT_POINT) -> None:
        self._disk_mount_point = disk_mount_point
        # (bytes_sent, bytes_recv, monotonic zaman) — ilk ölçümde None.
        self._previous_net: tuple[int, int, float] | None = None
        # psutil.cpu_percent'in taban değeri alındı mı.
        self._cpu_primed = False

    def collect(self) -> MetricSample:
        """Tek bir ölçüm alır.

        Fark gerektiren alanlar (cpu_percent, net_*) hesaplanamadığında None
        döner; çağıran bunu doğrudan null olarak kaydeder. 0.0 yazmak "yük
        yoktu" / "trafik yoktu" anlamına gelirdi ve ölçülemeyen bir anı sıfırla
        karıştırırdı.
        """
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self._disk_mount_point)
        net_sent_mb, net_recv_mb = self._network_rates()

        return MetricSample(
            uuid=str(uuid.uuid4()),
            measured_at=utc_now_iso(),
            cpu_percent=self._cpu_percent(),
            # "used" yerine total - available: available, çekirdeğin gerektiğinde
            # uygulamalara verebileceği belleği anlatır (cache dahil geri
            # alınabilir alanlar düşülmüş halde). Çöküş analizinde sorulan soru
            # "makinenin verebileceği bellek kalmış mıydı" olduğu için doğru
            # ölçü budur.
            ram_used_mb=(memory.total - memory.available) // BYTES_PER_MB,
            disk_percent=round(disk.percent, 1),
            net_sent_mb=net_sent_mb,
            net_recv_mb=net_recv_mb,
        )

    def _cpu_percent(self) -> float | None:
        """CPU kullanım yüzdesi — son çağrıdan bu yana geçen süre üzerinden.

        psutil.cpu_percent(interval=None) bloklamaz; iki çağrı arasındaki CPU
        zamanlarını karşılaştırır. İLK çağrının karşılaştıracağı bir taban
        olmadığı için 0.0 döner ve bu bir ölçüm değildir — o örnekte None
        verilir, sonraki her örnek geçerlidir.
        """
        value = psutil.cpu_percent(interval=None)
        if not self._cpu_primed:
            self._cpu_primed = True
            return None
        return round(value, 1)

    def _network_rates(self) -> tuple[float | None, float | None]:
        """Ağ trafiğini saniyede MB olarak döndürür (gönderilen, alınan).

        psutil sürekli artan toplam bayt sayacı verir; hız, iki ölçümün farkının
        geçen süreye bölünmesidir. İki durumda hesap yapılamaz ve None döner:
          * ilk ölçüm — karşılaştırılacak önceki sayaç yok,
          * sayacın geriye gitmesi — makine yeniden başlamış ve sayaç sıfırlanmış
            demektir; fark negatif çıkar ve anlamsızdır.
        """
        counters = psutil.net_io_counters()
        now = time.monotonic()
        previous = self._previous_net

        # Sayaçlar her durumda güncellenir: hesap yapılamayan bir ölçüm bile
        # bir SONRAKİ ölçümün tabanı olur.
        self._previous_net = (counters.bytes_sent, counters.bytes_recv, now)

        if previous is None:
            return None, None

        previous_sent, previous_recv, previous_time = previous
        elapsed = now - previous_time
        if elapsed <= 0:
            return None, None
        if counters.bytes_sent < previous_sent or counters.bytes_recv < previous_recv:
            return None, None

        sent_rate = (counters.bytes_sent - previous_sent) / BYTES_PER_MB / elapsed
        recv_rate = (counters.bytes_recv - previous_recv) / BYTES_PER_MB / elapsed
        return round(sent_rate, 3), round(recv_rate, 3)

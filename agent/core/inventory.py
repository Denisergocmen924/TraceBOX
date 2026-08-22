"""
Envanter — makinenin künyesi ve değişiklik tespiti.

Envanter, metriklerin aksine NADİREN değişir: işlemci modeli, çekirdek sayısı,
toplam RAM, disk, işletim sistemi sürümü. Bu yüzden ayrı bir uç noktaya gider
(POST /inventory) ve devices satırının üzerine yazılır — zaman serisi değildir.

Statik eklentiler (gpu_model, external_ip) M7'de bu modüle eklenecek.

M2 KAPSAMI: okuma ve karşılaştırma gerçek, gönderim yok.
"""

from __future__ import annotations

import platform
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

from agent import __version__
from agent.core.clock import epoch_to_utc_iso
from agent.core.config import Config
from agent.core.metrics import BYTES_PER_MB, DISK_MOUNT_POINT

# İşlemci modelinin okunduğu yer. platform.processor() Linux'ta çoğunlukla
# mimariyi ("x86_64") döndürdüğü için model adı buradan alınır.
CPUINFO_PATH = Path("/proc/cpuinfo")
CPUINFO_MODEL_KEY = "model name"


@dataclass(frozen=True)
class Inventory:
    """POST /inventory payload'ının veri kısmı (measured_at gönderim anında eklenir)."""

    cpu_model: str | None
    cpu_cores_physical: int | None
    cpu_cores_logical: int | None
    arch: str
    ram_total_mb: int
    disk_total_mb: int
    os_name: str | None
    os_version: str | None
    kernel_version: str
    last_boot: str
    agent_version: str
    enabled_addons: list[str]

    def as_dict(self) -> dict:
        """Karşılaştırmaya ve gönderime uygun sözlük hali.

        state.json'dan okunan known_inventory bir JSON nesnesidir; buradan çıkan
        sözlüğün de yalnızca JSON tiplerinden oluşması gerekir, yoksa eşitlik
        karşılaştırması (tuple vs list gibi) hep "değişmiş" derdi.
        """
        return asdict(self)


def collect_inventory(config: Config) -> Inventory:
    """Makinenin güncel künyesini okur."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(DISK_MOUNT_POINT)
    os_name, os_version = _os_release()

    return Inventory(
        cpu_model=_cpu_model(),
        # Fiziksel çekirdek sayısı bazı sanal makinelerde raporlanmaz; psutil
        # o durumda None döner ve sütun null kalır.
        cpu_cores_physical=psutil.cpu_count(logical=False),
        cpu_cores_logical=psutil.cpu_count(logical=True),
        arch=platform.machine(),
        ram_total_mb=memory.total // BYTES_PER_MB,
        disk_total_mb=disk.total // BYTES_PER_MB,
        os_name=os_name,
        os_version=os_version,
        kernel_version=platform.release(),
        last_boot=epoch_to_utc_iso(psutil.boot_time()),
        agent_version=__version__,
        enabled_addons=list(config.enabled_addons),
    )


def changed_fields(current: Inventory, known: dict) -> dict[str, tuple]:
    """Envanterin hangi alanlarının değiştiğini (eski, yeni) olarak döndürür.

    Boş sözlük "değişiklik yok" demektir. known boşsa (ilk çalıştırma) tüm
    alanlar değişmiş sayılır ve envanter mutlaka gönderilir.
    """
    current_dict = current.as_dict()
    return {
        field: (known.get(field), value)
        for field, value in current_dict.items()
        if known.get(field) != value
    }


def _cpu_model() -> str | None:
    """/proc/cpuinfo'dan işlemci modelini okur.

    Dosya yoksa ya da satır bulunamazsa platform.processor()'a düşer; o da
    boşsa None döner — envanterin geri kalanı bir alan yüzünden kaybolmaz.
    """
    try:
        with CPUINFO_PATH.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith(CPUINFO_MODEL_KEY):
                    _, _, value = line.partition(":")
                    if value.strip():
                        return value.strip()
    except OSError:
        pass

    return platform.processor() or None


def _os_release() -> tuple[str | None, str | None]:
    """İşletim sistemi adını ve sürümünü /etc/os-release'den okur.

    Dosya yoksa (Linux dışı ya da minimal sistem) ikisi de None döner; kernel
    sürümü ve mimari yine dolu olduğu için cihaz tanınabilir kalır.
    """
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return None, None

    return release.get("NAME"), release.get("VERSION_ID")

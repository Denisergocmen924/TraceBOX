"""
Yapılandırma okuyucu — config.toml'u okur, doğrular ve Config nesnesine çevirir.

Bu modül yalnızca OKUR. Agent'ın yazdığı tek dosya state.json'dır (state.py).
Alanların anlamı ve varsayılanları: CLAUDE.md §4.3.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Üretimdeki config yolu. TRACEBOX_CONFIG ortam değişkeni tanımlıysa onun değeri
# kullanılır, tanımlı değilse buraya düşülür — yani geliştirme override'ı opt-in,
# üretim yolu varsayılandır.
DEFAULT_CONFIG_PATH = Path("/etc/tracebox/config.toml")
CONFIG_PATH_ENV_VAR = "TRACEBOX_CONFIG"

# send_interval_seconds için alt sınır (CLAUDE.md §4.3). Config'e daha küçük bir
# değer yazılırsa sessizce yok sayılmaz: floor uygulanır ve uyarı basılır.
MIN_SEND_INTERVAL_SECONDS = 10

# Config'de bulunması ZORUNLU alanlar. Eksikse agent açılışta durur; varsayılan
# uydurmak, yanlış adrese veri göndermeye çalışan bir agent üretirdi.
REQUIRED_KEYS = ("collector_url", "device_key")


class ConfigError(Exception):
    """Config okunamadı, ayrıştırılamadı ya da zorunlu bir alan eksik."""


@dataclass(frozen=True)
class Config:
    """config.toml'un ayrıştırılmış hali.

    frozen=True: nesne oluşturulduktan sonra değiştirilemez. Döngü her tick'te
    dosyayı yeniden okuyup YENİ bir Config üretir; eldeki nesneyi kimse yerinde
    düzenlemez.
    """

    # --- Bağlantı ---
    collector_url: str
    device_key: str

    # --- Zamanlama (saniye) ---
    collect_interval_seconds: int = 5
    send_interval_seconds: int = 30
    command_poll_seconds: int = 10

    # --- Acil gönderim eşikleri (yüzde) ---
    flush_cpu_threshold: int = 90
    flush_ram_threshold: int = 90
    flush_disk_threshold: int = 95
    flush_cooldown_seconds: int = 20

    # --- Spool sınırları ---
    spool_max_age_days: int = 10
    spool_max_size_mb: int = 200

    # --- Eklentiler ---
    # Liste yerine tuple: frozen dataclass'ın içeriği de değiştirilemez olsun.
    enabled_addons: tuple[str, ...] = ()


def config_path() -> Path:
    """Kullanılacak config yolunu döndürür.

    Ortam değişkeni tanımlı ve boş değilse onu, aksi halde üretim yolunu verir.
    """
    override = os.environ.get(CONFIG_PATH_ENV_VAR, "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _positive_int(raw: dict, key: str, default: int) -> int:
    """raw[key]'i pozitif tam sayı olarak okur; yoksa default'a düşer.

    bool'u ayrıca eler: Python'da bool, int'in alt sınıfıdır, yani
    `isinstance(True, int)` doğrudur ve `collect_interval_seconds = true`
    yazılmış bir config sessizce 1 saniye olarak yorumlanırdı.
    """
    if key not in raw:
        return default

    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{key}' tam sayı olmalı, alınan: {value!r}")
    if value <= 0:
        raise ConfigError(f"'{key}' sıfırdan büyük olmalı, alınan: {value}")
    return value


def _parse(raw: dict, *, warn) -> Config:
    """Ayrıştırılmış TOML sözlüğünü doğrulanmış bir Config'e çevirir.

    warn: uyarı mesajlarını basan çağrılabilir (döngü stdout'a, testler listeye
    yazabilsin diye dışarıdan verilir).
    """
    for key in REQUIRED_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"zorunlu alan eksik veya boş: '{key}'")

    send_interval = _positive_int(raw, "send_interval_seconds", 30)
    if send_interval < MIN_SEND_INTERVAL_SECONDS:
        warn(
            f"send_interval_seconds={send_interval} alt sınırın altında; "
            f"{MIN_SEND_INTERVAL_SECONDS} kullanılıyor."
        )
        send_interval = MIN_SEND_INTERVAL_SECONDS

    addons = raw.get("enabled_addons", [])
    if not isinstance(addons, list) or not all(isinstance(a, str) for a in addons):
        raise ConfigError("'enabled_addons' string listesi olmalı")

    return Config(
        collector_url=raw["collector_url"].strip().rstrip("/"),
        device_key=raw["device_key"].strip(),
        collect_interval_seconds=_positive_int(raw, "collect_interval_seconds", 5),
        send_interval_seconds=send_interval,
        command_poll_seconds=_positive_int(raw, "command_poll_seconds", 10),
        flush_cpu_threshold=_positive_int(raw, "flush_cpu_threshold", 90),
        flush_ram_threshold=_positive_int(raw, "flush_ram_threshold", 90),
        flush_disk_threshold=_positive_int(raw, "flush_disk_threshold", 95),
        flush_cooldown_seconds=_positive_int(raw, "flush_cooldown_seconds", 20),
        spool_max_age_days=_positive_int(raw, "spool_max_age_days", 10),
        spool_max_size_mb=_positive_int(raw, "spool_max_size_mb", 200),
        enabled_addons=tuple(addons),
    )


class ConfigLoader:
    """Config dosyasını okur ve dosya değişene kadar önbellekte tutar.

    Döngü her tick'te (saniyede bir) load() çağırır; böylece kullanıcının
    config.toml'da yaptığı değişiklik servisi yeniden başlatmadan geçerli olur
    (CLAUDE.md §7). Her çağrıda dosyayı yeniden AYRIŞTIRMAK gereksiz olduğu için
    mtime + boyut karşılaştırılır, yalnızca değiştiyse yeniden okunur.
    """

    def __init__(self, path: Path | None = None, *, warn=print) -> None:
        self._path = path if path is not None else config_path()
        self._warn = warn
        self._cached: Config | None = None
        self._signature: tuple[float, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Config:
        """Güncel Config'i döndürür.

        İLK çağrı başarısız olursa ConfigError yükseltir — agent yanlış ya da
        eksik yapılandırmayla açılmaz. Sonraki çağrılarda hata olursa (kullanıcı
        dosyayı düzenlerken yarım kaydetmiş olabilir) son geçerli Config
        korunur ve uyarı basılır; çalışan agent bir yazım hatası yüzünden ölmez.
        """
        try:
            stat = self._path.stat()
            signature = (stat.st_mtime, stat.st_size)

            # Dosya son okumadan beri değişmediyse ayrıştırmayı atla.
            if self._cached is not None and signature == self._signature:
                return self._cached

            with self._path.open("rb") as handle:
                raw = tomllib.load(handle)

            config = _parse(raw, warn=self._warn)

        except (OSError, tomllib.TOMLDecodeError, ConfigError) as exc:
            if self._cached is None:
                raise ConfigError(f"{self._path} okunamadı: {exc}") from exc
            self._warn(f"config yeniden okunamadı ({exc}); önceki ayarlar sürüyor.")
            return self._cached

        self._cached = config
        self._signature = signature
        return config

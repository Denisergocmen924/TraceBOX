"""
Çalışma durumu — state.json'u okur ve yazar.

Bu dosyanın TEK YAZARI agent'tır. Collector da dashboard da
state.json'a dokunmaz; sunucu tarafındaki karşılıkları (devices.logging_enabled
gibi) ayrı sütunlardır ve agent'ın bildirdiğinin kopyasıdır.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

# Üretimdeki çalışma dizini. TRACEBOX_STATE_DIR tanımlıysa onun değeri kullanılır
# (config.py'deki override ile aynı mantık).
DEFAULT_STATE_DIR = Path("/var/lib/tracebox")
STATE_DIR_ENV_VAR = "TRACEBOX_STATE_DIR"

STATE_FILENAME = "state.json"
LOCK_FILENAME = "agent.lock"


@dataclass
class State:
    """Agent'ın yeniden başlatmalar arasında hatırladığı her şey."""

    # Buluta gönderim açık mı. pause komutu false, resume true yapar; toplama ve
    # spool'a yazma her iki durumda da sürer.
    logging_enabled: bool = True

    # En son gönderilmiş envanterin çekirdek alanları. Açılışta okunan envanter
    # bununla karşılaştırılır; fark yoksa POST /inventory hiç yapılmaz (M2).
    known_inventory: dict[str, Any] = field(default_factory=dict)

    # Son başarılı gönderimin wall-clock zamanı (ISO 8601 UTC).
    last_send: str | None = None

    # journald cursor'ı. Log okuyucu buradan devam eder; agent yeniden
    # başladığında ne log kaybolur ne de tekrarlanır (M4).
    journal_cursor: str | None = None

    # Son acil flush'ın zamanı — cooldown hesabı bunu kullanır (M7).
    last_flush_at: str | None = None

    # Uygulanmış ama collector tarafından henüz 200 ile onaylanmamış komut id'leri.
    # Sonraki /ingest gövdesinde ack olarak gider (M6).
    applied_command_ids: list[str] = field(default_factory=list)


def state_dir() -> Path:
    """Kullanılacak çalışma dizinini döndürür."""
    override = os.environ.get(STATE_DIR_ENV_VAR, "").strip()
    return Path(override) if override else DEFAULT_STATE_DIR


class StateStore:
    """state.json'u okuyup yazan tek nokta.

    Yazma ATOMİKTİR: önce aynı dizindeki geçici bir dosyaya yazılır, sonra
    os.replace ile hedefin üzerine taşınır. Aynı dosya sisteminde replace
    bölünmez bir işlemdir — güç kesintisi yazmanın ortasına denk gelse bile
    diskte ya eski ya yeni state bulunur, yarım JSON asla kalmaz.
    """

    def __init__(self, directory: Path | None = None, *, warn=print) -> None:
        self._dir = directory if directory is not None else state_dir()
        self._path = self._dir / STATE_FILENAME
        self._warn = warn

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> State:
        """state.json'u okur.

        Dosya yoksa (ilk çalıştırma) varsayılan State döner — known_inventory
        boş olduğu için envanter ilk turda mutlaka gönderilir.

        Dosya bozuksa okunamayan hali `.corrupt` uzantısıyla saklanır ve
        varsayılana dönülür: agent açılmama pahasına bozuk bir dosyayı korumaz,
        ama teşhis için de silmez.
        """
        if not self._path.exists():
            return State()

        try:
            with self._path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("state.json bir JSON nesnesi değil")
        except (OSError, ValueError) as exc:
            self._quarantine(exc)
            return State()

        # Yalnızca State'te TANIMLI alanlar alınır. Bilinmeyen anahtar sessizce
        # düşer (eski sürümden kalan alan agent'ı çökertmez), eksik anahtar
        # dataclass varsayılanını kullanır (yeni alan eklemek geriye dönük uyumlu
        # olsun diye).
        known = {f.name for f in fields(State)}
        return State(**{k: v for k, v in raw.items() if k in known})

    def save(self, state: State) -> None:
        """State'i diske atomik olarak yazar."""
        self._dir.mkdir(parents=True, exist_ok=True)

        # Geçici dosya HEDEFLE AYNI DİZİNDE olmalı: os.replace yalnızca aynı
        # dosya sistemi içinde atomiktir.
        tmp_path = self._path.with_name(f"{STATE_FILENAME}.tmp")

        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            # İçerik gerçekten diske insin; aksi halde replace atomik olsa bile
            # işletim sistemi tamponunda bekleyen veri güç kesintisinde kaybolur.
            os.fsync(handle.fileno())

        os.replace(tmp_path, self._path)

        # Dizin girdisinin kendisini de kalıcılaştır — üstteki fsync dosyanın
        # içeriğini garanti eder, adın yeni dosyaya bağlanmasını değil.
        dir_fd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _quarantine(self, exc: Exception) -> None:
        """Okunamayan state dosyasını kenara alır."""
        quarantine_path = self._path.with_name(f"{STATE_FILENAME}.corrupt")
        try:
            os.replace(self._path, quarantine_path)
            self._warn(f"state.json okunamadı ({exc}); {quarantine_path} olarak saklandı.")
        except OSError as move_error:
            self._warn(f"state.json okunamadı ({exc}) ve taşınamadı ({move_error}).")


class SingleWriterLock:
    """İkinci bir agent sürecinin aynı state üzerinde çalışmasını engeller.

    Tek yazar ilkesi yalnızca "hangi bileşen yazar" sorusu değildir; aynı
    makinede systemd servisi çalışırken elle `python -m agent` çağırmak da iki
    yazar üretir. flock ile alınan bu kilit süreç ölünce (çökme dahil) işletim
    sistemi tarafından bırakılır, yani artık kilit dosyası geride kalmaz.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / LOCK_FILENAME
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> SingleWriterLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            # LOCK_NB: kilit başkasındaysa bekleme, hemen hata ver. Beklemek iki
            # agent'ın sırayla çalışmasına yol açardı; istenen, ikincinin hiç
            # başlamaması.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise RuntimeError(
                f"başka bir agent süreci çalışıyor (kilit: {self._path})"
            ) from None

        os.write(fd, f"{os.getpid()}\n".encode())
        os.ftruncate(fd, len(f"{os.getpid()}\n"))
        self._fd = fd
        return self

    def __exit__(self, *exc_info) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

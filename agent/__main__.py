"""
Giriş noktası — `python -m agent` bu dosyayı çalıştırır.

systemd unit'i de aynı komutu çağırır (CLAUDE.md §8, adım 5). Buradaki tek iş
bağlamı kurup döngüye devretmek ve açılış hatalarını anlaşılır bir mesaja
çevirmek; iş mantığı agent.core içindedir.
"""

from __future__ import annotations

import sys

from agent.core import loop
from agent.core.config import ConfigError, ConfigLoader
from agent.core.state import SingleWriterLock, StateStore

# Çıkış kodları. systemd Restart=on-failure ile çalıştığı için sıfırdan farklı
# her kod yeniden başlatma tetikler (CLAUDE.md §8).
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_ALREADY_RUNNING = 2


def main() -> int:
    loader = ConfigLoader()
    store = StateStore()

    try:
        # Döngüden önce bir kez okunur: yapılandırma hatalı ya da eksikse agent
        # hiç açılmasın, yanlış ayarla çalışmasın.
        loader.load()
    except ConfigError as exc:
        print(f"config hatası: {exc}", file=sys.stderr, flush=True)
        return EXIT_CONFIG_ERROR

    try:
        # Kilit tüm çalışma boyunca tutulur; süreç bittiğinde bırakılır.
        with SingleWriterLock(store.directory):
            loop.run(loader, store)
    except RuntimeError as exc:
        print(f"başlatılamadı: {exc}", file=sys.stderr, flush=True)
        return EXIT_ALREADY_RUNNING
    except OSError as exc:
        print(f"dosya erişim hatası: {exc}", file=sys.stderr, flush=True)
        return EXIT_CONFIG_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

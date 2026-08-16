"""
Kalp atışı — agent'ın tek ve sonsuz döngüsü.

Döngü TEK thread'dir (CLAUDE.md §7): ikinci bir thread state.json'a ikinci bir
yazar eklerdi. Tüm işler tick sayaçlarıyla sıraya girer.

Tick sabit 1 saniyedir ve config'den okunmaz; collect/send/poll aralıkları
birbirinden bağımsız sayaçlardır (gerekçe: md/memory/decisions.md → "Döngü
tabanı").

M1 KAPSAMI: ağ yok, psutil yok, spool yok. Her iş yalnızca ne yapacağını basar.
İskeletin ritmini ve tek-yazar davranışını doğrulamak için.
"""

from __future__ import annotations

import signal
import threading
import time
from datetime import datetime, timezone

from agent import __version__
from agent.core.config import Config, ConfigLoader
from agent.core.state import State, StateStore

# Döngünün nabzı. Config'e AÇILMAZ: ölçüm sıklığı (insan sınırı) ile döngü ritmi
# (sistem sabiti) ayrı kavramlardır.
TICK_SECONDS = 1


def _utc_now_iso() -> str:
    """Wall-clock UTC zaman damgası (ISO 8601).

    Sayaçlar monotonic saat kullanır, damgalar bu fonksiyonu: biri "ne kadar
    zaman geçti", diğeri "hangi an" sorusunu cevaplar. Sistem saati geriye
    alındığında sayaçlar etkilenmez.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(message: str) -> None:
    """Tek satırlık zaman damgalı çıktı.

    flush=True: systemd altında stdout bir boruya (pipe) bağlıdır ve tamponlanır;
    tamponlanan satırlar journalctl'de dakikalarca görünmez.
    """
    print(f"{_utc_now_iso()} {message}", flush=True)


def _install_stop_signal() -> threading.Event:
    """SIGTERM/SIGINT geldiğinde kurulan olayı (event) döndürür.

    systemd durdurma isteğini SIGTERM ile gönderir. Sinyali yakalayıp döngüyü
    kendi turunun sonunda bitirmek, state yazmanın ortasında ölmeyi önler.
    """
    stop = threading.Event()

    def handle(signum, _frame) -> None:
        _log(f"[signal] {signal.Signals(signum).name} alındı, döngü kapanıyor.")
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    return stop


def _collect(config: Config) -> None:
    """Ölçüm ve log toplama adımı — M2/M4'te gerçek toplama buraya bağlanır.

    Pause'da da çalışır: pause yalnızca buluta göndermeyi durdurur, yerel kaydı
    değil (CLAUDE.md §7).
    """
    _log(f"[collect] ölçüm alınacak (her {config.collect_interval_seconds} sn) — M2")


def _poll_commands(config: Config) -> None:
    """Komut kuyruğunu yoklama adımı — M6'da GET /commands buraya bağlanır.

    Pause'da da çalışır; durmasaydı resume ve delete komutları cihaza hiç
    ulaşamazdı.
    """
    _log(f"[poll] komut sorulacak (her {config.command_poll_seconds} sn) — M6")


def _send(config: Config, state: State, store: StateStore) -> None:
    """Gönderim adımı — M3'te spool'un boşaltılması buraya bağlanır.

    M1'de yalnızca last_send'i güncelleyip state'i diske yazar; amaç tek yazarın
    ve atomik yazmanın gerçekten çalıştığını gözle görülür kılmak.
    """
    _log(f"[send] spool gönderilecek (her {config.send_interval_seconds} sn) — M3")
    state.last_send = _utc_now_iso()
    store.save(state)


def run(loader: ConfigLoader, store: StateStore) -> None:
    """Agent'ı açılıştan kapanışa kadar çalıştırır."""
    # --- AÇILIŞ (bir kez) ---
    config = loader.load()
    state = store.load()
    stop = _install_stop_signal()

    _log(f"[start] TraceBox agent {__version__}")
    _log(f"[start] config: {loader.path}")
    _log(f"[start] state:  {store.path}")
    _log(f"[start] hedef:  {config.collector_url}")
    _log(
        "[start] aralıklar: "
        f"collect={config.collect_interval_seconds}s "
        f"send={config.send_interval_seconds}s "
        f"poll={config.command_poll_seconds}s (tick={TICK_SECONDS}s)"
    )
    _log(f"[start] logging_enabled={state.logging_enabled}")
    _log("[start] envanter karşılaştırması — M2")

    # --- SAYAÇLAR ---
    # Her sayaç kendi "sıradaki çalışma anını" tutar. Karşılaştırmalar
    # monotonic saatle yapılır: sistem saati değişse bile döngü kilitlenmez.
    now = time.monotonic()
    next_collect = now  # ilk ölçüm beklemeden alınır
    next_poll = now + config.command_poll_seconds
    next_send = now + config.send_interval_seconds

    # --- KALP ATIŞI ---
    while not stop.is_set():
        now = time.monotonic()

        # Config her turda yeniden okunur; dosya değişmediyse önbellekten gelir.
        # Aralık değişikliği bir sonraki sayaç kurulumunda geçerli olur.
        config = loader.load()

        if now >= next_collect:
            _collect(config)
            next_collect = now + config.collect_interval_seconds

        if now >= next_poll:
            _poll_commands(config)
            next_poll = now + config.command_poll_seconds

        # Aşağısı yalnızca gönderim açıkken çalışır. Pause sırasında next_send
        # ileri ALINMAZ: süresi geçmiş halde bekler, böylece resume anında
        # birikmiş veri ilk turda çıkar.
        if state.logging_enabled and now >= next_send:
            _send(config, state, store)
            next_send = now + config.send_interval_seconds

        # sleep yerine wait: sinyal geldiğinde tick'in bitmesini beklemeden
        # uyanır, kapanma anında hissedilir gecikme olmaz.
        stop.wait(TICK_SECONDS)

    # --- KAPANIŞ ---
    # Durum diskte zaten günceldir (her save anında yazıldı); burada yalnızca
    # kapanışın temiz olduğu bildirilir.
    _log("[stop] döngü durdu.")

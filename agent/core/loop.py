"""
Kalp atışı — agent'ın tek ve sonsuz döngüsü.

Döngü TEK thread'dir: ikinci bir thread state.json'a ikinci bir
yazar eklerdi. Tüm işler tick sayaçlarıyla sıraya girer.

Tick sabit 1 saniyedir ve config'den okunmaz; collect/send/poll aralıkları
birbirinden bağımsız sayaçlardır.

M3 KAPSAMI: ölçümler spool'a yazılıp collector'a gönderilir. Loglar M4'te,
komut poll'u M6'da, acil flush M7'de bu döngüye bağlanacak.
"""

from __future__ import annotations

import signal
import threading
import time
from dataclasses import asdict

from agent import __version__
from agent.core import inventory as inventory_module
from agent.core.clock import utc_now_iso
from agent.core.config import Config, ConfigLoader
from agent.core.inventory import Inventory
from agent.core.metrics import MetricSample, MetricsCollector
from agent.core.shipper import Shipper
from agent.core.spool import RECORD_METRIC, Spool
from agent.core.state import State, StateStore

# Döngünün nabzı. Config'e AÇILMAZ: ölçüm sıklığı (insan sınırı) ile döngü ritmi
# (sistem sabiti) ayrı kavramlardır.
TICK_SECONDS = 1

# Ölçülemeyen alanların ekrandaki karşılığı. Kayıtta bu alanlar null olur;
# 0 yazmak "yük yoktu" demek olurdu.
UNAVAILABLE = "—"


def _log(message: str) -> None:
    """Tek satırlık zaman damgalı çıktı.

    flush=True: systemd altında stdout bir boruya (pipe) bağlıdır ve tamponlanır;
    tamponlanan satırlar journalctl'de dakikalarca görünmez.
    """
    print(f"{utc_now_iso()} {message}", flush=True)


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


def _format_sample(sample: MetricSample) -> str:
    """Ölçümü tek satırlık okunur bir özete çevirir."""

    def value(number, unit: str, digits: int = 1) -> str:
        return UNAVAILABLE if number is None else f"{number:.{digits}f}{unit}"

    return (
        f"cpu={value(sample.cpu_percent, '%')} "
        f"ram={sample.ram_used_mb}MB "
        f"disk={value(sample.disk_percent, '%')} "
        f"net↑{value(sample.net_sent_mb, 'MB/s', 3)} "
        f"net↓{value(sample.net_recv_mb, 'MB/s', 3)}"
    )


def _startup_inventory(config: Config, state: State) -> Inventory | None:
    """Envanteri okur; gönderilmesi gerekiyorsa onu döndürür, gerekmiyorsa None.

    Dönen değer gönderilene kadar bellekte bekler; `known_inventory` ancak
    collector'dan 200 alındıktan sonra yazılır.
    """
    current = inventory_module.collect_inventory(config)
    _log(
        f"[start] envanter: {current.os_name} {current.os_version} · "
        f"{current.cpu_model} · "
        f"{current.cpu_cores_physical}/{current.cpu_cores_logical} çekirdek · "
        f"{current.ram_total_mb}MB RAM · {current.disk_total_mb}MB disk · "
        f"kernel {current.kernel_version} ({current.arch})"
    )
    _log(f"[start] açılış zamanı: {current.last_boot}")

    changed = inventory_module.changed_fields(current, state.known_inventory)
    if not changed:
        _log("[start] envanter değişmemiş — gönderim gerekmiyor.")
        return None

    reason = "ilk kez okundu" if not state.known_inventory else "değişti"
    _log(
        f"[start] envanter {reason}: {len(changed)} alan "
        f"({', '.join(sorted(changed))}) — gönderilecek"
    )
    return current


def _collect(collector: MetricsCollector, spool: Spool) -> None:
    """Ölçüm alıp spool'a yazar.

    Pause'da da çalışır: pause yalnızca buluta göndermeyi durdurur, yerel kaydı
    değil.
    """
    sample = collector.collect()
    spool.add(RECORD_METRIC, asdict(sample))
    _log(f"[collect] {_format_sample(sample)}")


def _poll_commands(config: Config) -> None:
    """Komut kuyruğunu yoklama adımı — M6'da GET /commands buraya bağlanır.

    Pause'da da çalışır; durmasaydı resume ve delete komutları cihaza hiç
    ulaşamazdı.
    """
    _log(f"[poll] komut sorulacak (her {config.command_poll_seconds} sn) — M6")


def _send_inventory(
    shipper: Shipper, config: Config, state: State, store: StateStore, pending: Inventory
) -> Inventory | None:
    """Envanteri gönderir; 200 alınırsa state'e işler ve None döndürür."""
    result = shipper.send_inventory(config, pending.as_dict())
    if not result.ok:
        _log(f"[send] envanter gönderilemedi: {result.detail}")
        return pending

    state.known_inventory = pending.as_dict()
    store.save(state)
    _log("[send] envanter gönderildi.")
    return None


def _send_spool(
    shipper: Shipper, config: Config, state: State, store: StateStore, spool: Spool
) -> None:
    """Spool'u gönderir; başarılıysa last_send'i günceller."""
    result = shipper.send_pending(config, state.applied_command_ids)
    if not result.ok:
        _log(
            f"[send] gönderilemedi: {result.detail} — {spool.count()} kayıt bekliyor, "
            f"{shipper.backoff_seconds:.0f} sn sonra tekrar denenecek."
        )
        return

    if result.sent:
        state.last_send = utc_now_iso()
        store.save(state)

    _log(f"[send] {result.sent} kayıt gönderildi (spool: {spool.count()}).")


def run(loader: ConfigLoader, store: StateStore) -> None:
    """Agent'ı açılıştan kapanışa kadar çalıştırır."""
    # --- AÇILIŞ (bir kez) ---
    config = loader.load()
    state = store.load()
    stop = _install_stop_signal()
    collector = MetricsCollector()
    spool = Spool(
        store.directory,
        max_age_days=config.spool_max_age_days,
        max_size_mb=config.spool_max_size_mb,
    )
    shipper = Shipper(spool)

    _log(f"[start] TraceBox agent {__version__}")
    _log(f"[start] config: {loader.path}")
    _log(f"[start] state:  {store.path}")
    _log(f"[start] spool:  {spool.path} ({spool.count()} bekleyen kayıt)")
    _log(f"[start] hedef:  {config.collector_url}")
    _log(
        "[start] aralıklar: "
        f"collect={config.collect_interval_seconds}s "
        f"send={config.send_interval_seconds}s "
        f"poll={config.command_poll_seconds}s (tick={TICK_SECONDS}s)"
    )
    _log(f"[start] logging_enabled={state.logging_enabled}")
    pending_inventory = _startup_inventory(config, state)

    # --- SAYAÇLAR ---
    # Her sayaç kendi "sıradaki çalışma anını" tutar. Karşılaştırmalar
    # monotonic saatle yapılır: sistem saati değişse bile döngü kilitlenmez.
    now = time.monotonic()
    next_collect = now  # ilk ölçüm beklemeden alınır
    next_poll = now + config.command_poll_seconds
    next_send = now + config.send_interval_seconds

    # --- KALP ATIŞI ---
    try:
        while not stop.is_set():
            now = time.monotonic()

            # Config her turda yeniden okunur; dosya değişmediyse önbellekten
            # gelir. Aralık değişikliği bir sonraki sayaç kurulumunda geçerli olur.
            config = loader.load()

            if now >= next_collect:
                _collect(collector, spool)
                next_collect = now + config.collect_interval_seconds

            if now >= next_poll:
                _poll_commands(config)
                next_poll = now + config.command_poll_seconds

            # Aşağısı yalnızca gönderim açıkken çalışır. Pause sırasında
            # next_send ileri ALINMAZ: süresi geçmiş halde bekler, böylece
            # resume anında birikmiş veri ilk turda çıkar. Backoff sırasında da
            # aynı şekilde beklenir — süre dolunca ilk tick gönderimi yapar.
            if state.logging_enabled and now >= next_send and shipper.ready():
                if pending_inventory is not None:
                    pending_inventory = _send_inventory(
                        shipper, config, state, store, pending_inventory
                    )

                _send_spool(shipper, config, state, store, spool)
                next_send = now + config.send_interval_seconds

            # sleep yerine wait: sinyal geldiğinde tick'in bitmesini beklemeden
            # uyanır, kapanma anında hissedilir gecikme olmaz.
            stop.wait(TICK_SECONDS)
    finally:
        # --- KAPANIŞ ---
        # Durum diskte zaten günceldir (her save anında yazıldı); burada yalnızca
        # açık dosya ve bağlantılar kapatılır.
        shipper.close()
        spool.close()
        _log("[stop] döngü durdu.")

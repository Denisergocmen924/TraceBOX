"""
Kalp atışı — agent'ın tek ve sonsuz döngüsü.

Döngü TEK thread'dir (CLAUDE.md §7): ikinci bir thread state.json'a ikinci bir
yazar eklerdi. Tüm işler tick sayaçlarıyla sıraya girer.

Tick sabit 1 saniyedir ve config'den okunmaz; collect/send/poll aralıkları
birbirinden bağımsız sayaçlardır (gerekçe: md/memory/decisions.md → "Döngü
tabanı").

M2 KAPSAMI: ölçüm ve envanter gerçek; ağ, spool ve gönderim yok. Toplanan her
örnek ekrana basılır ve düşer.
"""

from __future__ import annotations

import signal
import threading
import time

from agent import __version__
from agent.core import inventory as inventory_module
from agent.core.clock import utc_now_iso
from agent.core.config import Config, ConfigLoader
from agent.core.metrics import MetricSample, MetricsCollector
from agent.core.state import State, StateStore

# Döngünün nabzı. Config'e AÇILMAZ: ölçüm sıklığı (insan sınırı) ile döngü ritmi
# (sistem sabiti) ayrı kavramlardır.
TICK_SECONDS = 1

# Ölçülemeyen alanların ekrandaki karşılığı. Kayıtta bu alanlar null olur;
# 0 yazmak "yük yoktu" demek olurdu (md/memory/decisions.md → "Ağ metriği").
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


def _report_inventory(config: Config, state: State) -> None:
    """Açılışta envanteri okur ve state'teki bilinen haliyle karşılaştırır.

    known_inventory M2'de GÜNCELLENMEZ: envanterin "gönderilmiş" sayılması için
    collector'dan 200 alınmış olması gerekir, o da M3'ün işi (açık spec boşluğu
    #3). Bu yüzden M2'de her açılışta "değişti" görülmesi beklenen davranıştır.
    """
    current = inventory_module.collect_inventory(config)
    _log(
        f"[start] envanter: {current.os_name} {current.os_version} · "
        f"{current.cpu_model} · {current.cpu_cores_physical}/{current.cpu_cores_logical} çekirdek · "
        f"{current.ram_total_mb}MB RAM · {current.disk_total_mb}MB disk · "
        f"kernel {current.kernel_version} ({current.arch})"
    )
    _log(f"[start] açılış zamanı: {current.last_boot}")

    changed = inventory_module.changed_fields(current, state.known_inventory)
    if not changed:
        _log("[start] envanter değişmemiş — gönderim gerekmiyor.")
        return

    reason = "ilk kez okundu" if not state.known_inventory else "değişti"
    _log(f"[start] envanter {reason}: {len(changed)} alan ({', '.join(sorted(changed))}) — gönderim M3")


def _collect(collector: MetricsCollector) -> None:
    """Ölçüm alma adımı.

    Pause'da da çalışır: pause yalnızca buluta göndermeyi durdurur, yerel kaydı
    değil (CLAUDE.md §7). M3'te örnek burada spool'a yazılacak.
    """
    sample = collector.collect()
    _log(f"[collect] {_format_sample(sample)}")


def _poll_commands(config: Config) -> None:
    """Komut kuyruğunu yoklama adımı — M6'da GET /commands buraya bağlanır.

    Pause'da da çalışır; durmasaydı resume ve delete komutları cihaza hiç
    ulaşamazdı.
    """
    _log(f"[poll] komut sorulacak (her {config.command_poll_seconds} sn) — M6")


def _send(config: Config, state: State, store: StateStore) -> None:
    """Gönderim adımı — M3'te spool'un boşaltılması buraya bağlanır.

    Şimdilik yalnızca last_send'i güncelleyip state'i diske yazar; amaç tek
    yazarın ve atomik yazmanın gerçekten çalıştığını gözle görülür kılmak.
    """
    _log(f"[send] spool gönderilecek (her {config.send_interval_seconds} sn) — M3")
    state.last_send = utc_now_iso()
    store.save(state)


def run(loader: ConfigLoader, store: StateStore) -> None:
    """Agent'ı açılıştan kapanışa kadar çalıştırır."""
    # --- AÇILIŞ (bir kez) ---
    config = loader.load()
    state = store.load()
    stop = _install_stop_signal()
    collector = MetricsCollector()

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
    _report_inventory(config, state)

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
            _collect(collector)
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

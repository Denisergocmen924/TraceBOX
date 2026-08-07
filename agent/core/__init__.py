"""
agent.core — platformdan bağımsız çekirdek.

Buradaki hiçbir modül journald, systemd ya da Windows Event Log gibi bir
kaynağı doğrudan tanımaz. Log okuma her zaman agent.logsources içindeki
LogSource arayüzü üzerinden yapılır (CLAUDE.md §2, "bağımlılığı izole et").

MODÜLLER (Milestone sırasına göre eklenecek):
    config.py     M1  config.toml'u OKUR. Agent config'e asla yazmaz —
                      config insan sınırıdır (CLAUDE.md §2).
    state.py      M1  state.json'u OKUR ve YAZAR. Bu dosyanın TEK YAZARI'dır;
                      "single writer" ilkesi burada somutlaşır.
    loop.py       M1  Tick tabanlı kalp atışı. Tek döngü, sayaçlarla — iki
                      thread yok, çünkü ikinci bir yazar single writer'ı bozar.
    metrics.py    M2  psutil ile ölçüm toplama.
    inventory.py  M2  Açılışta envanter okuma + değişiklik tespiti.
    spool.py      M3  SQLite tabanlı disk kuyruğu (ring buffer).
    shipper.py    M3  Batch'leme, POST, ack, backoff (at-least-once).
    commands.py   M6  pause / resume / delete uygulama.
    flush.py      M7  Eşik aşımında acil gönderim + cooldown.
"""

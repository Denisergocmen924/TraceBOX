"""
Collector sürümü — tek satırlık ayrı modül.

Sabit `main.py`'de dururdu; oradan alınamaz çünkü `main` zaten
`endpoints_ingest`'i içe aktarıyor ve ters yönde bir içe aktarma **döngüsel
import** yaratır (`main` yarı kurulmuşken `COLLECTOR_VERSION` henüz tanımlı
değildir). Sürümü kimsenin bağımlı olmadığı ayrı bir modüle almak, döngüyü
kırmanın en sade yolu.
"""

from __future__ import annotations

# Agent'ın bildirdiği agent_version'dan bağımsız, collector'ın kendi sürümü.
# Ayakta olan sürüm GET /verify üzerinden doğrulanır — kimliksiz uçlar (/ ve
# /health) sürüm döndürmez.
COLLECTOR_VERSION = "0.2.0"

"""
TraceBox Collector — FastAPI uygulamasının giriş noktası.

Collector, cihazların buluta açılan tek yazma kapısıdır (CLAUDE.md §1):

    [Agent] --device key/TLS--> [Collector: Fly.io] --service key--> [Supabase]

Bağlı router'lar:
  endpoints_ingest.py    POST /inventory, POST /ingest, GET /verify  (device key)

Sonraki milestone'larda eklenecek:
  M5 -> endpoints_device.py    POST /devices                         (user JWT)
  M6 -> endpoints_commands.py  GET  /commands                        (device key)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import supabase_client
from endpoints_ingest import router as ingest_router

# Kendi logger'larımızın (tracebox.*) satırları `fly logs` çıktısına düşsün;
# uvicorn yalnızca kendi logger'larını yapılandırır.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Agent'ın bildirdiği agent_version'dan bağımsız, collector'ın kendi sürümü.
# Ayakta olan sürüm GET /health üzerinden doğrulanır.
COLLECTOR_VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Supabase istemcisini açılışta kurar, kapanışta kapatır.

    Ortam değişkenleri eksikse kurulum burada hata verir: süreç ayağa kalkmaz,
    sağlık kontrolü geçmez ve deploy bir önceki sürümde kalır.
    """
    supabase_client.init_client()
    yield
    await supabase_client.close_client()


app = FastAPI(
    title="TraceBox Collector",
    version=COLLECTOR_VERSION,
    lifespan=lifespan,
    # MVP boyunca açık; endpoint'leri elle denemeyi kolaylaştırır. Üretime
    # çıkarken kapatılacak.
    docs_url="/docs",
)

app.include_router(ingest_router)


@app.get("/")
async def root() -> dict:
    """Kimlik uç noktası — kimlik doğrulaması yok, veri döndürmez."""
    return {"service": "tracebox-collector", "version": COLLECTOR_VERSION}


@app.get("/health")
async def health() -> dict:
    """Sağlık kontrolü — Fly.io bu uç noktayı düzenli olarak yoklar.

    Supabase bağlantısı burada DENENMEZ: veritabanındaki geçici bir kesinti,
    ayakta olan sürecin yeniden başlatılmasına yol açmamalıdır.
    """
    return {"status": "ok"}

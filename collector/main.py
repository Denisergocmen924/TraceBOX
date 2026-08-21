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
from version import COLLECTOR_VERSION

# Kendi logger'larımızın (tracebox.*) satırları `fly logs` çıktısına düşsün;
# uvicorn yalnızca kendi logger'larını yapılandırır.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


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
    # Belge uçlarının ÜÇÜ BİRDEN kapatılır. Yalnızca docs_url=None yazmak
    # yetmez: /redoc ve /openapi.json FastAPI varsayılanı olarak açık kalır ve
    # asıl içerik (tam API sözleşmesi) /openapi.json'dadır — /docs ise onu
    # görüntüleyen arayüzden ibarettir. Sözleşme public repo'da zaten açık;
    # kapatmanın amacı gizlilik değil, canlı adresin kimliksiz konuşmaması
    # (bkz. security_bugs.md B1).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(ingest_router)


@app.get("/")
async def root() -> dict:
    """Servis adı — kimlik doğrulaması yok.

    Sürüm BİLEREK yok: kimliksiz bir uçtan verilen sürüm numarası, saldırganın
    ilk adımı olan "hedefi tanıma"yı bedava kolaylaştırır. Sürüm, zaten cihaz
    anahtarıyla korumalı olan GET /verify yanıtında (security_bugs.md B2).
    """
    return {"service": "tracebox-collector"}


@app.get("/health")
async def health() -> dict:
    """Sağlık kontrolü — Fly.io bu uç noktayı düzenli olarak yoklar.

    Supabase bağlantısı burada DENENMEZ: veritabanındaki geçici bir kesinti,
    ayakta olan sürecin yeniden başlatılmasına yol açmamalıdır.
    """
    return {"status": "ok"}

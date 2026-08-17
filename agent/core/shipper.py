"""
Shipper — spool'daki kayıtları collector'a gönderir.

Teslim garantisi **at-least-once**: kayıt yalnızca 200 alındıktan sonra
spool'dan silinir. Ağ hatası tekrar göndermeye yol açabilir; her kaydın taşıdığı
UUID sayesinde sunucu tekrarı eler (CLAUDE.md §11, Boşluk C).

Başarısız denemeler üstel backoff ile seyrekleşir: kapalı bir collector'a her
turda yüklenmek ne veriyi kurtarır ne de bağlantıyı geri getirir.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from agent.core.spool import RECORD_CRASH, RECORD_LOG, RECORD_METRIC, Spool

INVENTORY_PATH = "/inventory"
INGEST_PATH = "/ingest"

# Bir istekte gönderilen kayıt sayısı. Collector tablo başına 1000 satır kabul
# ediyor; 500 bunun altında kalır ve tek bir isteğin gövdesini küçük tutar.
BATCH_ROWS = 500

# Bir gönderim turunda atılacak azami istek sayısı. Birikmiş spool tek turda
# boşaltılmaya çalışılsaydı döngü dakikalarca gönderime kilitlenirdi.
MAX_BATCHES_PER_CYCLE = 5

# Collector'ın yanıt vermesi için beklenen süre. Aşılırsa kayıtlar spool'da
# kalır ve bir sonraki turda tekrar denenir (CLAUDE.md §7).
REQUEST_TIMEOUT_SECONDS = 10.0

BACKOFF_INITIAL_SECONDS = 10.0
BACKOFF_MAX_SECONDS = 300.0

# Spool tür etiketinin wire gövdesindeki karşılığı (CLAUDE.md §4.2).
PAYLOAD_KEYS = {
    RECORD_METRIC: "metrics",
    RECORD_LOG: "logs",
    RECORD_CRASH: "crash_snapshots",
}


@dataclass(frozen=True)
class SendResult:
    """Bir gönderim turunun sonucu."""

    ok: bool
    sent: int = 0
    detail: str = ""


class Shipper:
    """Collector'a HTTP gönderimi ve backoff durumu."""

    def __init__(self, spool: Spool) -> None:
        self._spool = spool
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._backoff_seconds = 0.0
        # Bir sonraki denemenin en erken zamanı (monotonic).
        self._retry_at = 0.0

    @property
    def backoff_seconds(self) -> float:
        return self._backoff_seconds

    def ready(self) -> bool:
        """Backoff süresi dolduysa True."""
        return time.monotonic() >= self._retry_at

    def send_inventory(self, config, inventory: dict) -> SendResult:
        """Envanteri gönderir. Yalnızca 200 alındığında ok=True döner."""
        ok, detail = self._post(config, INVENTORY_PATH, inventory)
        return SendResult(ok=ok, detail=detail)

    def send_pending(self, config, applied_command_ids: list[str]) -> SendResult:
        """Spool'u eskiden yeniye gönderir.

        Komut ack'leri yalnızca ilk isteğe eklenir; aynı listeyi her batch'te
        tekrarlamanın faydası yok.
        """
        sent = 0
        acks = list(applied_command_ids)

        for _ in range(MAX_BATCHES_PER_CYCLE):
            records = self._spool.take(BATCH_ROWS)
            if not records and not acks:
                break

            payload = _build_payload(records, acks)
            ok, detail = self._post(config, INGEST_PATH, payload)
            if not ok:
                return SendResult(ok=False, sent=sent, detail=detail)

            # 200 alındı: kayıtlar artık sunucuda, spool'dan düşebilirler.
            self._spool.ack([record.uuid for record in records])
            sent += len(records)
            acks = []

            if len(records) < BATCH_ROWS:
                break

        return SendResult(ok=True, sent=sent)

    def close(self) -> None:
        self._client.close()

    def _post(self, config, path: str, payload: dict) -> tuple[bool, str]:
        """İsteği atar ve backoff durumunu günceller."""
        url = f"{config.collector_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {config.device_key}"}

        try:
            response = self._client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as error:
            return self._failure(f"bağlanılamadı ({error.__class__.__name__})")

        if response.status_code == 200:
            self._success()
            return True, ""

        if response.status_code == 401:
            return self._failure("cihaz anahtarı reddedildi (401)")

        return self._failure(f"HTTP {response.status_code}")

    def _success(self) -> None:
        self._backoff_seconds = 0.0
        self._retry_at = 0.0

    def _failure(self, detail: str) -> tuple[bool, str]:
        self._backoff_seconds = min(
            max(BACKOFF_INITIAL_SECONDS, self._backoff_seconds * 2), BACKOFF_MAX_SECONDS
        )
        self._retry_at = time.monotonic() + self._backoff_seconds
        return False, detail


def _build_payload(records, acks: list[str]) -> dict:
    """Spool kayıtlarını POST /ingest gövdesine dönüştürür."""
    payload: dict = {key: [] for key in PAYLOAD_KEYS.values()}
    payload["applied_command_ids"] = acks

    for record in records:
        payload[PAYLOAD_KEYS[record.type]].append(record.payload)

    return payload

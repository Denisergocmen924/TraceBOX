"""
Cihazdan gelen yazma uçları: POST /inventory, POST /ingest, GET /verify.

Üçü de cihaz anahtarı ile korunur. Payload'da `device_id` YOKTUR; satırlara
`device_id` ve `account_id` doğrulanmış anahtardan eklenir (CLAUDE.md §11,
Boşluk A).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from auth import AuthenticatedDevice, DeviceIdentity
from supabase_client import SupabaseError, get_client

router = APIRouter()

# Tek istekte kabul edilen azami satır sayısı (tablo başına). Agent spool'u
# 200 MB'a kadar birikebildiği için gönderim bu boyutta parçalara bölünür.
MAX_ROWS_PER_TABLE = 1000

# Payload alan adı ile sütun adının ayrıştığı tek yer: agent'ın ürettiği UUID
# tabloda birincil anahtardır (CLAUDE.md §4.2 / §5).
UUID_FIELD = "uuid"
ID_COLUMN = "id"


class _Payload(BaseModel):
    """Ortak model ayarları.

    `extra="forbid"`: sözleşmede olmayan bir alan sessizce yutulmaz, 422 döner.
    Yazım hatası taşıyan bir alan aksi halde null olarak kaydedilirdi.
    """

    model_config = ConfigDict(extra="forbid")


class InventoryIn(_Payload):
    """POST /inventory gövdesi — `devices` satırının üzerine yazılır."""

    cpu_model: str | None = None
    cpu_cores_physical: int | None = None
    cpu_cores_logical: int | None = None
    arch: str | None = None
    ram_total_mb: int | None = None
    disk_total_mb: int | None = None
    os_name: str | None = None
    os_version: str | None = None
    kernel_version: str | None = None
    last_boot: AwareDatetime | None = None
    agent_version: str | None = None
    gpu_model: str | None = None
    external_ip: str | None = None
    enabled_addons: list[str] = Field(default_factory=list)


class MetricIn(_Payload):
    """Bir ölçüm örneği. Eklenti alanları kapalıysa null gelir."""

    uuid: UUID
    measured_at: AwareDatetime
    cpu_percent: float | None = None
    ram_used_mb: int | None = None
    disk_percent: float | None = None
    net_sent_mb: float | None = None
    net_recv_mb: float | None = None
    temperature_c: float | None = None
    swap_used_mb: int | None = None
    load_avg_1: float | None = None
    load_avg_5: float | None = None
    load_avg_15: float | None = None
    gpu_usage_percent: float | None = None
    gpu_vram_used_mb: int | None = None


class LogIn(_Payload):
    """Bir log kaydı. `level` şemadaki check kısıtıyla aynı dört değeri alır."""

    uuid: UUID
    measured_at: AwareDatetime
    level: Literal["info", "warning", "error", "critical"]
    message: str
    source: str | None = None


class ProcessIn(_Payload):
    """Çöküş anındaki bir süreç — `crash_snapshots.processes` içine gömülür."""

    name: str
    cpu: float
    ram_mb: int


class CrashSnapshotIn(_Payload):
    """Acil flush anında alınan süreç görüntüsü."""

    uuid: UUID
    measured_at: AwareDatetime
    trigger_reason: Literal["cpu", "ram", "disk", "log"] | None = None
    processes: list[ProcessIn] = Field(default_factory=list)


class IngestIn(_Payload):
    """POST /ingest gövdesi — üç tablo ve komut ack'i tek istekte gelir."""

    metrics: Annotated[list[MetricIn], Field(max_length=MAX_ROWS_PER_TABLE)] = Field(
        default_factory=list
    )
    logs: Annotated[list[LogIn], Field(max_length=MAX_ROWS_PER_TABLE)] = Field(
        default_factory=list
    )
    crash_snapshots: Annotated[
        list[CrashSnapshotIn], Field(max_length=MAX_ROWS_PER_TABLE)
    ] = Field(default_factory=list)
    # M6'da işlenecek: ack edilen komutlar 'applied' yapılacak, delete komutu
    # cihaz satırının silinmesini tetikleyecek (CLAUDE.md §6).
    applied_command_ids: list[UUID] = Field(default_factory=list)


@router.post("/inventory")
async def post_inventory(payload: InventoryIn, device: AuthenticatedDevice) -> dict:
    """Envanteri cihaz satırına yazar.

    Envanter zaman serisi değildir: her gönderim bir öncekinin üzerine yazar.
    """
    fields = payload.model_dump(mode="json")
    fields["last_seen"] = _server_now()

    await _write(lambda: get_client().update_device(device.id, fields))
    return {"status": "ok", "device_name": device.device_name}


@router.post("/ingest")
async def post_ingest(payload: IngestIn, device: AuthenticatedDevice) -> dict:
    """Ölçüm, log ve çöküş kayıtlarını yazar.

    Tablolar ayrı isteklerle yazılır; ortada bir hata olursa istemci tüm batch'i
    tekrar gönderir ve daha önce yazılmış satırlar `id` çakışmasıyla elenir
    (CLAUDE.md §11, Boşluk C).
    """
    client = get_client()
    tables = (
        ("metrics", payload.metrics),
        ("logs", payload.logs),
        ("crash_snapshots", payload.crash_snapshots),
    )

    for table, items in tables:
        rows = [_row(item, device) for item in items]
        await _write(lambda table=table, rows=rows: client.insert_rows(table, rows))

    await _write(lambda: client.update_device(device.id, {"last_seen": _server_now()}))

    return {
        "status": "ok",
        "accepted": {table: len(items) for table, items in tables},
    }


@router.get("/verify")
async def get_verify(device: AuthenticatedDevice) -> dict:
    """Kurulum sonu bağlantı testi — anahtar geçerliyse 200 (CLAUDE.md §8)."""
    return {"status": "ok", "device_name": device.device_name}


def _row(item: BaseModel, device: DeviceIdentity) -> dict[str, Any]:
    """Payload kaydını tablo satırına çevirir: uuid → id, kimlik alanları eklenir."""
    row = item.model_dump(mode="json")
    row[ID_COLUMN] = row.pop(UUID_FIELD)
    row["device_id"] = device.id
    row["account_id"] = device.account_id
    return row


def _server_now() -> str:
    """`last_seen` için sunucu saati.

    Agent'ın damgası kullanılmaz: saati kaymış bir cihaz aksi halde offline
    tespitini yanıltırdı (md/memory/decisions.md → "Envanterin measured_at'i").
    """
    return datetime.now(timezone.utc).isoformat()


async def _write(operation) -> None:
    """Supabase çağrısını çalıştırır; hatayı 503'e çevirir.

    503, agent'a "veriyi tut, sonra tekrar dene" demektir; spool kaydı ancak 200
    sonrası silinir (CLAUDE.md §7).
    """
    try:
        await operation()
    except SupabaseError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kayıt şu an yazılamıyor.",
        ) from error

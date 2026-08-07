"""
BlackBox Collector — FastAPI uygulamasının giriş noktası.

Collector, cihazların buluta açılan TEK YAZMA kapısıdır (CLAUDE.md §1):

    [Agent] --device key/TLS--> [Collector: Fly.io] --service key--> [Supabase]

Neden agent doğrudan Supabase'e yazmıyor:
  * Supabase'e yazmak için service key gerekir; onu her makineye dağıtmak,
    tek bir cihazın ele geçirilmesini TÜM veritabanının ele geçirilmesi
    haline getirirdi.
  * Cihaz anahtarları iptal edilebilir ve cihaz başına ayrıdır; service key
    değildir.
  * Şema doğrulama, idempotency ve komut ack'i gibi kurallar tek yerde durur.

M0 KAPSAMI (şu an burası): sadece "hello" + sağlık kontrolü. Amaç, Fly.io
deploy hattının uçtan uca çalıştığını KOD YAZMADAN ÖNCE kanıtlamak. İnce dikey
dilim mantığı bu: önce boru hattı, sonra içinden akan veri.

SONRAKİ MILESTONE'LARDA BURAYA BAĞLANACAK ROUTER'LAR:
  M3 -> endpoints_ingest.py    POST /inventory, POST /ingest   (device key)
  M5 -> endpoints_device.py    POST /devices                   (user JWT)
  M6 -> endpoints_commands.py  GET  /commands                  (device key)
"""

from fastapi import FastAPI

# Agent'ın POST /inventory ile bildirdiği agent_version'dan bağımsız, collector'ın
# kendi sürümü. Deploy'un gerçekten güncellendiğini / hangi sürümün ayakta
# olduğunu GET /health üzerinden doğrulamak için var.
COLLECTOR_VERSION = "0.1.0"

app = FastAPI(
    title="BlackBox Collector",
    version=COLLECTOR_VERSION,
    # Otomatik dokümantasyon MVP boyunca açık: endpoint'leri elle test etmeyi
    # (özellikle M3'te ilk gerçek ingest denemesini) çok kolaylaştırıyor.
    # Üretime çıkarken kapatılacak — /docs, saldırgana API haritasını
    # bedavaya vermek demektir.
    docs_url="/docs",
)


@app.get("/")
async def root() -> dict:
    """Kimlik uç noktası.

    Kimlik doğrulaması YOK ve olmayacak: hiçbir veri sızdırmaz, sadece
    "buraya doğru geldin" der. Yanlış URL'e istek atan bir agent'ın hatayı
    hızlı görmesini sağlar.
    """
    return {"service": "blackbox-collector", "version": COLLECTOR_VERSION}


@app.get("/health")
async def health() -> dict:
    """Sağlık kontrolü — Fly.io bu uç noktayı düzenli olarak yoklar.

    KAPSAM KARARI: burada Supabase bağlantısı DENENMEZ. Sağlık kontrolü
    "bu süreç istek karşılayabiliyor mu?" sorusuna cevap vermeli. Supabase'i de
    yoklasaydık, geçici bir veritabanı kesintisi Fly'ın makineyi sağlıksız
    sayıp yeniden başlatmasına yol açardı — yani dışarıdaki bir arıza, bizim
    ayakta olan servisimizi de yıkardı. Bağımlılık sağlığı ayrı bir uç
    noktanın işi (gerekirse ileride /health/deps).
    """
    return {"status": "ok"}

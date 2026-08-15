# =============================================================================
# TraceBox Collector — container image (python:3.12-slim tabanlı).
# =============================================================================
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: yorumlayıcı .pyc dosyası üretmez.
# PYTHONUNBUFFERED: stdout tamponlanmaz, log satırları yazıldıkları anda dışarı
#   verilir — süreç çökse bile son satırlar `fly logs` çıktısına düşer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# requirements.txt tek başına, koddan önce kopyalanır: bu dosya değişmediği
# sürece Docker aşağıdaki pip install katmanını önbellekten alır ve adımı atlar.
COPY requirements.txt .

# --no-cache-dir: pip indirdiği paket arşivlerini image içinde saklamaz.
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodu en son katmanda kopyalanır; en sık değişen içerik olduğu için
# üstündeki katmanların önbelleği bozulmaz.
COPY . .

# uid 10001 ile yetkisiz bir kullanıcı açılır, /app ona devredilir ve sonraki
# tüm komutlar (CMD dahil) root yerine bu kullanıcıyla çalışır.
RUN useradd --create-home --uid 10001 collector \
    && chown -R collector:collector /app
USER collector

# fly.toml'daki internal_port ile aynı port.
EXPOSE 8080

# --host 0.0.0.0: container dışından, yani Fly proxy'sinden gelen bağlantılar
#   kabul edilir; uvicorn'un varsayılanı olan 127.0.0.1 yalnızca container
#   içinden erişilebilir olurdu.
# Tek uvicorn worker'ı çalışır; eşzamanlılık asyncio döngüsünden gelir.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

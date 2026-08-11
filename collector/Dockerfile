# =============================================================================
# TraceBox Collector — Fly.io için container image
#
# Neden slim (alpine değil): alpine musl libc kullanır; uvloop/httptools gibi
# derlenmiş paketlerde hazır wheel bulunmaz, her build'de kaynaktan derleme
# gerekir. Slim, birkaç MB fazlasına karşılık dakikalarca build süresi kazandırır.
#
# Neden 3.12 (3.13/3.14 değil): C uzantılı paketlerin (uvloop, httptools) hazır
# wheel'lerinin en oturmuş olduğu sürüm. Collector'da en yeni Python
# özelliklerine ihtiyacımız yok — burada sıkıcı olmak bir erdem.
# =============================================================================
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: container'da .pyc dosyası yazmanın anlamı yok
#   (image katmanı salt-okunur, süreç yeniden başlayınca zaten kaybolur).
# PYTHONUNBUFFERED: stdout'u tamponlama. Tamponlanırsa `fly logs` çıktısı
#   gecikmeli gelir; süreç çökerse son satırlar hiç görünmez — bir log-shipping
#   projesinde kendi loglarımızı kaybetmek özellikle kötü olurdu.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# KATMAN ÖNBELLEĞİ: önce SADECE requirements.txt kopyalanır ve kurulum yapılır.
# Kod değişip requirements değişmediğinde Docker bu katmanı önbellekten alır ve
# pip install adımı tamamen atlanır. Kodla birlikte kopyalasaydık her küçük
# değişiklik tüm bağımlılıkları yeniden indirtirdi.
COPY requirements.txt .
# --no-cache-dir: pip'in indirdiği paketleri image içinde saklamasını engeller,
# bir daha kullanılmayacak onlarca MB'ı image'dan çıkarır.
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodu — en sık değişen şey, bu yüzden en son katmanda.
COPY . .

# ROOT OLMAYAN KULLANICI: bir güvenlik açığında saldırganın container içinde
# root yetkisi bulmasını engeller. Collector, internete açık ve kimliği
# doğrulanmamış istek kabul eden tek bileşenimiz — en çok burada gerekli.
RUN useradd --create-home --uid 10001 collector \
    && chown -R collector:collector /app
USER collector

# fly.toml'daki internal_port ile AYNI olmalı; farklı olursa Fly trafiği
# boşluğa yönlendirir ve deploy sağlık kontrolünde takılır.
EXPOSE 8080

# --host 0.0.0.0 ZORUNLU: varsayılan 127.0.0.1 sadece container içinden
# erişilebilir olurdu, Fly'ın proxy'si bağlanamazdı.
# Tek worker: Fly'da yatay ölçekleme makine sayısıyla yapılır, süreç sayısıyla
# değil. Ayrıca 256 MB RAM'de çoklu worker'ın maliyeti kazancından fazla.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

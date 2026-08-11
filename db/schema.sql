-- =============================================================================
-- TraceBox — db/schema.sql
-- 6 tablo + indeksler + foreign key'ler.
--
-- ÇALIŞTIRMA SIRASI:  schema.sql  ->  triggers.sql  ->  rls.sql
-- Nerede: Supabase Dashboard -> SQL Editor -> New query -> yapıştır -> Run.
--
-- TEMEL İLKE (CLAUDE.md §5):
--   accounts.id == auth.users.id  (AYNI UUID).
--   Bu sayede her RLS politikası tek bir basit karşılaştırmaya iner:
--   "satırın account_id'si == auth.uid() mi?"  -> join yok, alt sorgu yok.
--
-- DENORMALİZASYON KARARI:
--   metrics / logs / crash_snapshots tablolarında hem device_id hem account_id var.
--   account_id teorik olarak device_id üzerinden türetilebilir (fazlalık bilgi), ama
--   bilerek kopyalıyoruz çünkü:
--     1) RLS politikası devices tablosuna join atmak zorunda kalmıyor (her satır
--        okumasında join = ölçeklenmeyen maliyet),
--     2) retention job'ı (pg_cron) doğrudan accounts.retention_days ile eşleşiyor.
--   Bedeli: satır başına 16 byte. Kazancı: her okumada join'den kurtulmak.
--
-- ZAMAN DAMGASI KARARI:
--   measured_at değerini AGENT koyar, sunucu değil. Çünkü ilgilendiğimiz an
--   "olayın makinede olduğu an"; "sunucuya ulaştığı an" değil. Spool'da 2 saat
--   bekleyip sonra gönderilen bir metrik, 2 saat önceki zaman damgasıyla durmalı.
--
-- SİLME DAVRANIŞI:
--   Tüm FK'ler ON DELETE CASCADE. Cihaz silinince verisi de gider; hesap silinince
--   her şey gider. Yetim (orphan) satır bırakmıyoruz.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- GELİŞTİRME SIRASINDA SIFIRLAMA
-- Şemayı baştan kurmak istersen aşağıdaki bloğun yorumunu kaldırıp çalıştır.
-- DİKKAT: tüm veriyi siler. Üretimde ASLA çalıştırma.
-- -----------------------------------------------------------------------------
-- drop table if exists commands        cascade;
-- drop table if exists crash_snapshots cascade;
-- drop table if exists logs            cascade;
-- drop table if exists metrics         cascade;
-- drop table if exists devices         cascade;
-- drop table if exists accounts        cascade;


-- =============================================================================
-- 1) accounts — Supabase Auth kullanıcısının uygulamaya özel uzantısı
-- =============================================================================
-- auth.users tablosuna dokunamayız (Supabase'in yönettiği şema), bu yüzden
-- kendi alanlarımızı burada tutuyoruz. Satır otomatik açılır: triggers.sql
-- içindeki on_auth_user_created trigger'ı yeni kullanıcı kaydında ekler.
create table accounts (
  id             uuid primary key references auth.users(id) on delete cascade,

  -- POLICY (sistem sınırı), config (insan sınırı) DEĞİL — CLAUDE.md §2.
  -- Kullanıcı bunu dashboard'dan değiştiremez: rls.sql'de accounts için UPDATE
  -- politikası bilerek tanımlanmadı. Sadece plan yükseltmesiyle (service key ile)
  -- değişir. Retention job'ı bu değeri okur.
  retention_days integer     not null default 10,

  -- TraceBox'ın kendi ürün planı (Supabase'in faturalandırma planıyla ilgisi yok).
  -- Bugün tek değer var; ileride 'pro' eklenince retention_days bununla birlikte
  -- yükselecek.
  plan           text        not null default 'free',

  created_at     timestamptz not null default now()
);


-- =============================================================================
-- 2) devices — izlenen makine: kimlik + envanter + durum
-- =============================================================================
-- Bir satır = bir kurulu agent. Cihazın "kim olduğu" burada; agent bunu bilmez,
-- sadece anahtarını sunar (CLAUDE.md §11 / Boşluk A).
create table devices (
  -- device_id'yi SUNUCU üretir. Agent kendi kimliğini asla iddia edemez; sahte
  -- device_id ile başkasının verisine yazma yolu böylece kapanır.
  id                  uuid primary key default gen_random_uuid(),
  account_id          uuid not null references accounts(id) on delete cascade,
  device_name         text not null,

  -- Cihaz anahtarının SHA-256'sı. DÜZ ANAHTAR HİÇBİR ZAMAN SAKLANMAZ.
  -- POST /devices anahtarı bir kez döner; DB sızsa bile anahtarlar kullanılamaz.
  -- Doğrulama: collector sha256(gelen_anahtar) hesaplar, bu sütunla eşleştirir.
  key_hash            text not null,

  -- --- envanter çekirdeği (POST /inventory ile yazılır, üzerine yazma) ---------
  -- Hepsi nullable: agent bir alanı okuyamazsa (kısıtlı VM, egzotik donanım)
  -- envanterin tamamı reddedilmesin, elindekini yazsın.
  cpu_model           text,
  cpu_cores_physical  integer,
  cpu_cores_logical   integer,
  arch                text,
  ram_total_mb        integer,
  disk_total_mb       integer,
  os_name             text,
  os_version          text,
  kernel_version      text,

  -- --- statik eklentiler (sadece config'de enabled_addons içindeyse dolu) ------
  gpu_model           text,
  external_ip         text,

  -- --- durum -----------------------------------------------------------------
  last_boot           timestamptz,
  agent_version       text,

  -- Cihazda hangi eklentilerin açık olduğu. jsonb dizi: ["swap","load_avg"].
  -- Neden jsonb (ayrı tablo değil): sabit, kısa, sadece bütün olarak okunan bir
  -- liste. İlişkisel açmak fayda getirmez — over-engineering yok (§2).
  enabled_addons      jsonb   not null default '[]',

  -- Pause durumunun SUNUCU KOPYASI. Tek doğruluk kaynağı agent'ın state.json'ı
  -- (single writer); bu sütun sadece dashboard'ın rozet göstermesi için var.
  logging_enabled     boolean not null default true,

  -- Cihazdan en son ne zaman istek geldi. Offline tespiti bunun üzerinden yapılır
  -- (ör. now() - last_seen > 2 dk => offline rozeti).
  last_seen           timestamptz,

  -- Delete akışının ara durumu (CLAUDE.md §6 sıralama kuralı):
  -- Dashboard "sil" dediğinde satır HEMEN silinmez — bir 'delete' komutu kuyruğa
  -- girer ve bu bayrak true olur. Agent komutu alıp kendini temizler, ack'ler;
  -- satırı collector ack anında siler. Erken silseydik agent'ın anahtarı
  -- geçersizleşir, 401 alır ve delete komutunu hiç göremezdi.
  pending_delete      boolean not null default false,

  created_at          timestamptz not null default now()
);

-- Dashboard'ın en sık sorgusu: "bu hesabın cihazları".
create index devices_account_id_idx on devices (account_id);

-- Her istekte sha256(anahtar) ile arama yapılıyor. Unique olması hem O(log n)
-- arama sağlar hem de iki cihazın aynı anahtara sahip olmasını imkânsız kılar.
create unique index devices_key_hash_key on devices (key_hash);

-- Aynı hesap içinde aynı isimli iki cihaz olamaz. Cihazları isimden ayırt eden
-- kullanıcı için "laptop" ve "laptop" karışıklığını en baştan önler.
-- POST /devices bu ihlalde 409 dönecek (M5).
-- Kapsam account_id ile sınırlı: farklı kullanıcılar aynı ismi kullanabilir.
create unique index devices_account_name_key on devices (account_id, device_name);


-- =============================================================================
-- 3) metrics — zaman serisi ölçümler
-- =============================================================================
-- ŞEKİL KARARI: "SAF A" = düz sütunlar (wide table), key/value satırları değil.
-- Metrik kümesi sabit ve küçük; düz sütun hem daha az yer kaplar hem de
-- dashboard sorguları (avg, max, zaman aralığı) doğrudan çalışır. Key/value
-- şeması esneklik verirdi ama burada esnekliğe ihtiyaç yok (§2 fit-for-purpose).
create table metrics (
  -- Bu UUID'yi AGENT üretir (default yok — collector id'siz satır yazamaz).
  -- Idempotency'nin temeli (CLAUDE.md §11 / Boşluk C): shipper at-least-once
  -- çalışır, yani ack kaybolursa aynı batch tekrar gönderilir. Sunucu
  -- INSERT ... ON CONFLICT (id) DO NOTHING ile tekrarı sessizce eler.
  id                uuid primary key,
  device_id         uuid not null references devices(id)  on delete cascade,
  account_id        uuid not null references accounts(id) on delete cascade,
  measured_at       timestamptz not null,

  -- --- çekirdek (her zaman toplanır) -----------------------------------------
  cpu_percent       real,
  ram_used_mb       integer,
  disk_percent      real,
  -- Ağ: kümülatif sayaç DEĞİL, RATE. Agent iki ölçüm arasındaki farkı alıp
  -- geçen süreye böler. Ham sayaç gönderilseydi her makine yeniden başladığında
  -- sayaç sıfırlanır ve grafikte anlamsız negatif sıçrama olurdu.
  net_sent_mb       real,
  net_recv_mb       real,

  -- --- eklentiler (config'de kapalıysa null kalır) ----------------------------
  temperature_c     real,
  swap_used_mb      integer,
  -- load average Linux'a özgü; Windows agent'ında null kalacak. Sütunu şimdiden
  -- açıyoruz ki platform genişlediğinde şema değişmesin (§2 "en geniş ortak payda").
  load_avg_1        real,
  load_avg_5        real,
  load_avg_15       real,
  gpu_usage_percent real,
  gpu_vram_used_mb  integer
);

-- Dashboard'ın tek sorgu şekli: "şu cihazın şu zaman aralığındaki metrikleri".
-- Sütun sırası önemli: önce eşitlik (device_id), sonra aralık (measured_at).
create index metrics_device_measured_idx on metrics (device_id, measured_at);


-- =============================================================================
-- 4) logs — normalize edilmiş sistem logları
-- =============================================================================
-- Agent'ın LogSource arayüzü (agent/logsources/base.py) journald / eventlog gibi
-- OS'a özgü çıktıları buradaki sabit şekle çevirir. Tablo hiçbir OS detayı bilmez.
create table logs (
  id           uuid primary key,                        -- idempotency (bkz. metrics.id)
  device_id    uuid not null references devices(id)  on delete cascade,
  account_id   uuid not null references accounts(id) on delete cascade,
  measured_at  timestamptz not null,

  -- SADECE 4 seviye. journald'ın 8 PRIORITY değeri agent tarafında bunlara
  -- indirgenir. Az seviye = tutarlı filtre + basit UI. CHECK constraint bunu
  -- veritabanı seviyesinde garanti eder: bozuk seviye asla giremez.
  level        text not null check (level in ('info','warning','error','critical')),

  -- Kırpma YOK. Stack trace'in son satırı çoğu zaman en önemli satırdır; ortadan
  -- kesilen bir hata mesajı işe yaramaz. Postgres text sütunu zaten TOAST ile
  -- büyük değerleri sıkıştırıp satır dışına taşır.
  message      text not null,

  -- Logu üreten servis/birim (journald'da _SYSTEMD_UNIT). Her kaynakta yok, nullable.
  source       text
);

create index logs_device_measured_idx on logs (device_id, measured_at);


-- =============================================================================
-- 5) crash_snapshots — acil flush anının fotoğrafı
-- =============================================================================
-- Sadece eşik aşıldığında (cpu/ram/disk yüksek veya error/critical log) yazılır.
-- "Makine neden boğuldu?" sorusunun cevabı: o anda kim kaynak yiyordu.
create table crash_snapshots (
  id             uuid primary key,                      -- idempotency
  device_id      uuid not null references devices(id)  on delete cascade,
  account_id     uuid not null references accounts(id) on delete cascade,
  measured_at    timestamptz not null,

  -- Flush'ı hangi eşik tetikledi: 'cpu' | 'ram' | 'disk' | 'log'.
  -- İSİM NOTU: bu sütun CLAUDE.md §4.2'de "trigger" olarak geçiyordu. TRIGGER
  -- SQL standardında reserved word; Postgres tırnaksız kabul etse de ORM ve
  -- migration araçlarında sürekli kaçış gerektiren bir mayın. Adı değiştirildi
  -- ve wire payload'ındaki alan adı da aynı şekilde güncellendi (tutarlılık).
  -- CHECK yok: yeni bir tetikleyici türü eklendiğinde (ör. 'manual') veri kaybı
  -- yaşamayalım — level'dan farklı olarak bu alan filtre değil, açıklama.
  trigger_reason text,

  -- [{"name":"chrome","cpu":42.1,"ram_mb":2100}, ...]
  -- Süreç listesi değişken uzunlukta ve sadece bütün olarak okunuyor; ayrı tablo
  -- açmak join maliyeti getirir, karşılığında hiçbir sorgu kazandırmaz.
  processes      jsonb not null
);

create index crash_snapshots_device_measured_idx on crash_snapshots (device_id, measured_at);


-- =============================================================================
-- 6) commands — dashboard'dan agent'a kontrol kuyruğu
-- =============================================================================
-- AKIŞ YÖNÜ: dashboard INSERT eder (pending) -> agent GET /commands ile çeker ->
-- uygular -> bir sonraki /ingest'te id'yi ack'ler -> collector 'applied' yapar.
--
-- NEDEN KUYRUK, NEDEN PUSH DEĞİL: agent NAT arkasında, dinleyen portu yok ve
-- olması da istenmiyor (saldırı yüzeyi). Agent'ın dışarı çıkması, dışarıdan
-- agent'a bağlanılmasından hem güvenli hem basit. Bedeli ~10 sn gecikme.
create table commands (
  -- Burada default var: komutu SUNUCU üretir (metrics/logs'un tersi).
  -- Bu id aynı zamanda agent tarafında idempotency anahtarı: aynı komut iki kez
  -- gelirse (ack kaybolduysa) agent onu tekrar uygulamaz.
  id           uuid primary key default gen_random_uuid(),
  device_id    uuid not null references devices(id)  on delete cascade,
  account_id   uuid not null references accounts(id) on delete cascade,

  -- pause  : buluta gönderimi durdur (yerel toplama DEVAM eder — CLAUDE.md §7)
  -- resume : gönderime devam et, spool'da birikeni eskiden yeniye boşalt
  -- delete : agent'ın tam temizliği / self-uninstall
  type         text not null check (type in ('pause','resume','delete')),

  -- pending -> applied. Tek yönlü; geri dönüş yok. Yeniden denemek isteyen
  -- dashboard yeni bir komut satırı ekler.
  status       text not null default 'pending' check (status in ('pending','applied')),

  created_at   timestamptz not null default now(),
  applied_at   timestamptz
);

-- Agent'ın 10 saniyede bir attığı sorgu tam olarak bu: bu cihazın bekleyen
-- komutları. En sık çalışan sorgu olduğu için indeks tam bu şekle uyuyor.
create index commands_device_status_idx on commands (device_id, status);

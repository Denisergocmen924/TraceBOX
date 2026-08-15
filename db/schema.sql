-- =============================================================================
-- TraceBox — db/schema.sql
-- 6 tablo, indeksler ve foreign key'ler.
--
-- accounts.id ile auth.users.id aynı UUID'dir; RLS politikaları bu sayede
-- doğrudan "account_id = auth.uid()" karşılaştırmasına iner (CLAUDE.md §5).
--
-- metrics / logs / crash_snapshots tablolarında account_id, device_id'nin
-- yanında denormalize tutulur: satırın sahibi devices tablosuna join atılmadan
-- bilinir.
--
-- measured_at değerini agent yazar; satırın sunucuya ulaştığı anı değil,
-- ölçümün makinede alındığı anı gösterir.
--
-- Tüm foreign key'ler ON DELETE CASCADE: cihaz silinince ölçümleri, hesap
-- silinince tüm cihazları ve verileri birlikte silinir.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Aşağıdaki bloğun yorumu kaldırılırsa altı tablo da verileriyle birlikte
-- silinir ve şema sıfırdan kurulabilir hale gelir.
-- DİKKAT: tüm veriyi siler.
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
-- Satırı triggers.sql içindeki on_auth_user_created trigger'ı ekler: auth.users
-- tablosuna her yeni kayıt düştüğünde aynı id ile burada bir satır açılır.
create table accounts (
  id             uuid primary key references auth.users(id) on delete cascade,

  -- Retention job'ının (retention.sql) okuduğu değer: bu hesabın satırları kaç
  -- gün sonra silinecek. rls.sql'de accounts için UPDATE politikası tanımlı
  -- olmadığından dashboard üzerinden değiştirilemez — policy, config değil.
  retention_days integer     not null default 10,

  -- TraceBox'ın kendi ürün planı; Supabase'in faturalandırma planından bağımsız.
  plan           text        not null default 'free',

  created_at     timestamptz not null default now()
);


-- =============================================================================
-- 2) devices — izlenen makine: kimlik + envanter + durum
-- =============================================================================
-- Bir satır = kurulu bir agent. Agent kendi device_id'sini bilmez; yalnızca
-- anahtarını sunar, sunucu satırı anahtardan bulur (CLAUDE.md §11 / Boşluk A).
create table devices (
  -- Değeri sunucu üretir; agent'ın gönderdiği hiçbir alan bu id'yi belirlemez.
  id                  uuid primary key default gen_random_uuid(),
  account_id          uuid not null references accounts(id) on delete cascade,
  device_name         text not null,

  -- Cihaz anahtarının SHA-256 özeti; anahtarın düz hali saklanmaz. Doğrulama
  -- sırasında collector gelen anahtarın özetini hesaplar ve bu sütunla
  -- constant-time karşılaştırır.
  key_hash            text not null,

  -- --- envanter çekirdeği (POST /inventory ile yazılır, üzerine yazma) ---------
  -- Hepsi nullable: agent bir alanı okuyamazsa o alan null kalır, envanterin
  -- geri kalanı yine de yazılır.
  cpu_model           text,
  cpu_cores_physical  integer,
  cpu_cores_logical   integer,
  arch                text,
  ram_total_mb        integer,
  disk_total_mb       integer,
  os_name             text,
  os_version          text,
  kernel_version      text,

  -- --- statik eklentiler (config'de enabled_addons içindeyse dolu) -------------
  gpu_model           text,
  external_ip         text,

  -- --- durum -----------------------------------------------------------------
  last_boot           timestamptz,
  agent_version       text,

  -- Cihazda açık olan eklentiler, jsonb dizi olarak: ["swap","load_avg"].
  enabled_addons      jsonb   not null default '[]',

  -- Pause durumunun sunucu kopyası; dashboard rozeti bu sütunu okur. Tek
  -- doğruluk kaynağı agent'ın state.json dosyasıdır (single writer).
  logging_enabled     boolean not null default true,

  -- Cihazdan en son ne zaman istek geldiği. Offline tespiti bu değere bakar
  -- (ör. now() - last_seen > 2 dk ise offline sayılır).
  last_seen           timestamptz,

  -- Delete akışının ara durumu (CLAUDE.md §6). Dashboard "sil" dediğinde satır
  -- hemen silinmez: kuyruğa bir 'delete' komutu girer ve bu bayrak true olur.
  -- Satırı, agent komutu uygulayıp ack'ledikten sonra collector siler.
  pending_delete      boolean not null default false,

  created_at          timestamptz not null default now()
);

-- Dashboard'ın "bu hesabın cihazları" sorgusunu karşılar.
create index devices_account_id_idx on devices (account_id);

-- Her istekte sha256(anahtar) ile arama yapılır. Unique olması aynı anahtarın
-- iki cihaza yazılmasını da engeller.
create unique index devices_key_hash_key on devices (key_hash);

-- Aynı hesap içinde iki cihaz aynı adı taşıyamaz; ihlalde POST /devices 409
-- döner (M5). Kapsam account_id ile sınırlı — farklı hesaplar aynı adı
-- kullanabilir.
create unique index devices_account_name_key on devices (account_id, device_name);


-- =============================================================================
-- 3) metrics — zaman serisi ölçümler
-- =============================================================================
-- Bir satır = bir ölçüm anı. Metrikler düz sütunlarda tutulur (key/value satırı
-- değil), böylece avg/max ve zaman aralığı sorguları doğrudan çalışır.
create table metrics (
  -- UUID'yi agent üretir; sütunun default'u yoktur, id'siz satır yazılamaz.
  -- Shipper at-least-once çalıştığından aynı batch tekrar gönderilebilir;
  -- collector INSERT ... ON CONFLICT (id) DO NOTHING ile tekrarı eler
  -- (CLAUDE.md §11 / Boşluk C).
  id                uuid primary key,
  device_id         uuid not null references devices(id)  on delete cascade,
  account_id        uuid not null references accounts(id) on delete cascade,
  measured_at       timestamptz not null,

  -- --- çekirdek (her zaman toplanır) -----------------------------------------
  cpu_percent       real,
  ram_used_mb       integer,
  disk_percent      real,

  -- Ağ değerleri kümülatif sayaç değil rate'tir: agent iki ölçüm arasındaki
  -- farkı geçen süreye böler ve sonucu buraya yazar.
  net_sent_mb       real,
  net_recv_mb       real,

  -- --- eklentiler (config'de kapalıysa null kalır) ----------------------------
  temperature_c     real,
  swap_used_mb      integer,

  -- load average yalnızca Linux'ta okunur; diğer platformlarda null kalır.
  load_avg_1        real,
  load_avg_5        real,
  load_avg_15       real,

  gpu_usage_percent real,
  gpu_vram_used_mb  integer
);

-- Dashboard'ın sorgu şekli: "şu cihazın şu zaman aralığındaki metrikleri".
-- Sütun sırası bu şekle uyar: önce eşitlik (device_id), sonra aralık.
create index metrics_device_measured_idx on metrics (device_id, measured_at);


-- =============================================================================
-- 4) logs — normalize edilmiş sistem logları
-- =============================================================================
-- Agent'ın LogSource arayüzü (agent/logsources/base.py) journald gibi OS'a özgü
-- çıktıları buradaki dört alana çevirir; tablo hiçbir OS detayı tutmaz.
create table logs (
  id           uuid primary key,                        -- agent üretir (bkz. metrics.id)
  device_id    uuid not null references devices(id)  on delete cascade,
  account_id   uuid not null references accounts(id) on delete cascade,
  measured_at  timestamptz not null,

  -- Dört seviye kabul edilir; journald'ın 8 PRIORITY değeri agent tarafında
  -- bunlara indirgenir. CHECK, listede olmayan bir seviyenin yazılmasını
  -- veritabanı düzeyinde engeller.
  level        text not null check (level in ('info','warning','error','critical')),

  -- Mesaj kırpılmadan saklanır. Postgres büyük değerleri TOAST ile sıkıştırıp
  -- satır dışına taşır.
  message      text not null,

  -- Logu üreten servis/birim (journald'da _SYSTEMD_UNIT). Her kaynakta
  -- bulunmadığı için nullable.
  source       text
);

create index logs_device_measured_idx on logs (device_id, measured_at);


-- =============================================================================
-- 5) crash_snapshots — acil flush anının fotoğrafı
-- =============================================================================
-- Satır yalnızca bir eşik aşıldığında yazılır (cpu/ram/disk yüksek ya da
-- error/critical seviyeli log geldi) ve o anda kaynak tüketen süreçleri tutar.
create table crash_snapshots (
  id             uuid primary key,                      -- agent üretir
  device_id      uuid not null references devices(id)  on delete cascade,
  account_id     uuid not null references accounts(id) on delete cascade,
  measured_at    timestamptz not null,

  -- Flush'ı tetikleyen eşik: 'cpu' | 'ram' | 'disk' | 'log'. Sütun adı
  -- CLAUDE.md §4.2'de "trigger" olarak geçer; TRIGGER SQL'de reserved word
  -- olduğu için hem şemada hem wire payload'ında trigger_reason kullanılır.
  -- CHECK tanımlı değil: yeni bir tetikleyici türü eklendiğinde satır reddedilmez.
  trigger_reason text,

  -- [{"name":"chrome","cpu":42.1,"ram_mb":2100}, ...] biçiminde, değişken
  -- uzunlukta süreç listesi; yalnızca bütün olarak okunur.
  processes      jsonb not null
);

create index crash_snapshots_device_measured_idx on crash_snapshots (device_id, measured_at);


-- =============================================================================
-- 6) commands — dashboard'dan agent'a kontrol kuyruğu
-- =============================================================================
-- Akış: dashboard satırı 'pending' olarak INSERT eder -> agent GET /commands ile
-- çeker -> uygular -> bir sonraki /ingest gövdesinde id'yi ack'ler -> collector
-- satırı 'applied' yapar. Agent dinleyen port açmaz; komutlar yalnızca bu
-- kuyruğun yoklanmasıyla ulaşır.
create table commands (
  -- Bu id'yi sunucu üretir (metrics/logs'un tersine default vardır). Agent aynı
  -- id'yi ikinci kez gördüğünde komutu tekrar uygulamaz.
  id           uuid primary key default gen_random_uuid(),
  device_id    uuid not null references devices(id)  on delete cascade,
  account_id   uuid not null references accounts(id) on delete cascade,

  -- pause  : buluta gönderimi durdurur, yerel toplama sürer (CLAUDE.md §7)
  -- resume : gönderime devam eder, spool'da birikeni eskiden yeniye boşaltır
  -- delete : agent'ın tam temizliği / self-uninstall
  type         text not null check (type in ('pause','resume','delete')),

  -- pending -> applied yönünde tek yönlü ilerler; geri dönüşü yoktur. Komutu
  -- yeniden denemek için dashboard yeni bir satır ekler.
  status       text not null default 'pending' check (status in ('pending','applied')),

  created_at   timestamptz not null default now(),
  applied_at   timestamptz
);

-- Agent'ın komut poll'unda attığı sorgunun şekli: bu cihazın bekleyen komutları.
create index commands_device_status_idx on commands (device_id, status);

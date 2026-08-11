-- =============================================================================
-- TraceBox — db/rls.sql
-- Row-Level Security politikaları + kolon bazlı yetkiler.
--
-- ÇALIŞTIRMA SIRASI:  schema.sql  ->  triggers.sql  ->  [rls.sql]
--
-- İKİ AYRI YAZMA/OKUMA YOLU (CLAUDE.md §11 / Boşluk D):
--
--   YAZMA:  Agent -> Collector (Fly.io) -> Supabase   [service key]
--           service_role RLS'i BYPASS eder. Bu bilinçli: collector'ın kimin
--           adına yazdığını cihaz anahtarından zaten çözmüş oluyor, ikinci bir
--           kontrol katmanı fayda getirmez.
--
--   OKUMA:  Dashboard -> Supabase doğrudan               [user JWT / anon key]
--           RLS ZORUNLU. Tarayıcıdaki koda güvenilmez; kullanıcı istediği
--           sorguyu atabilir. Tek savunma hattı buradaki politikalardır.
--
-- POLİTİKA ŞABLONU: her tabloda aynı soru — "satırın account_id'si == auth.uid() mi?"
--
-- PERFORMANS NOTU: auth.uid() her yerde (select auth.uid()) olarak sarmalandı.
-- Çıplak çağrıda Postgres fonksiyonu HER SATIR için yeniden çalıştırır; alt sorgu
-- olarak sarınca sorgu başına BİR kez değerlendirip (InitPlan) sonucu önbelleğe
-- alır. 100 bin satırlık metrik sorgusunda ölçülebilir fark yaratır.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1) RLS'i etkinleştir
-- -----------------------------------------------------------------------------
-- KRİTİK: RLS açık AMA politika tanımlı değilse tablo "herkese kapalı" olur
-- (varsayılan reddet). Yani bu blok tek başına çalıştırılırsa dashboard hiçbir
-- şey göremez. Bu dosya bir bütün olarak çalıştırılmalı.
alter table public.accounts        enable row level security;
alter table public.devices         enable row level security;
alter table public.metrics         enable row level security;
alter table public.logs            enable row level security;
alter table public.crash_snapshots enable row level security;
alter table public.commands        enable row level security;


-- -----------------------------------------------------------------------------
-- 2) accounts — sadece OKUMA
-- -----------------------------------------------------------------------------
-- accounts.id == auth.users.id olduğu için karşılaştırma doğrudan id üzerinden.
create policy sel_accounts on public.accounts
  for select
  using (id = (select auth.uid()));

-- UPDATE politikası BİLEREK YOK: retention_days ve plan birer POLICY'dir
-- (sistem sınırı), kullanıcının değiştirebileceği bir config değil (CLAUDE.md §2).
-- Kullanıcı kendi retention'ını 3650 güne çekebilseydi fatura mantrası
-- ("az yaz, seyrek çek, kısa sakla") anlamsızlaşırdı.
-- INSERT politikası da yok: satırı yalnızca handle_new_user() trigger'ı açar.
-- DELETE politikası da yok: hesap silme auth.users üzerinden CASCADE ile olur.


-- -----------------------------------------------------------------------------
-- 3) devices — oku / adını değiştir / (force remove için) sil
-- -----------------------------------------------------------------------------
create policy sel_devices on public.devices
  for select
  using (account_id = (select auth.uid()));

-- UPDATE: satır düzeyinde kendi cihazları.
-- Postgres kuralı: UPDATE politikasında WITH CHECK verilmezse USING ifadesi hem
-- görünürlük hem de yeni satır kontrolü için kullanılır. Yani kullanıcı bir
-- cihazı başka bir hesaba TAŞIYAMAZ; ayrıca ek bir ifade yazmaya gerek yok.
create policy upd_devices on public.devices
  for update
  using (account_id = (select auth.uid()));

-- DELETE: "force remove" — agent'ı çevrimdışı olmuş, delete komutunu asla
-- alamayacak bir cihazı kaydından düşürmek için (CLAUDE.md §6).
-- CASCADE ile metrics/logs/crash_snapshots/commands da temizlenir.
-- UYARI (kabul edilen bedel): makine sonradan geri gelirse agent 401 alır ve
-- kendini kapatana kadar boşuna dener; yerel dosyaları elle temizlenmelidir.
-- Normal yol her zaman 'delete' komutudur; bu yalnızca kaçış kapısı.
create policy del_devices on public.devices
  for delete
  using (account_id = (select auth.uid()));


-- -----------------------------------------------------------------------------
-- 3b) devices — KOLON BAZLI UPDATE kısıtı
-- -----------------------------------------------------------------------------
-- SORUN: yukarıdaki upd_devices politikası SATIR düzeyinde çalışır; hangi
-- SÜTUNLARIN yazılabileceğini söylemez. Tek başına bırakılırsa kullanıcı kendi
-- cihaz satırının key_hash / last_seen / logging_enabled / pending_delete
-- alanlarını da yazabilir. Bu:
--   * "single writer" ilkesini bozar (bu alanların yazarı collector'dır),
--   * last_seen'i elle ileri atarak offline tespitini yanıltmayı,
--   * key_hash'i değiştirip kendi agent'ını kilitlemeyi mümkün kılar.
-- ÇÖZÜM: yetkiyi SQL GRANT seviyesinde daraltmak. RLS ve GRANT bağımsız iki
-- katman; bir UPDATE'in geçmesi için İKİSİNİN DE izin vermesi gerekir.
--
-- Supabase varsayılan olarak public şemadaki tablolarda anon+authenticated
-- rollerine geniş yetki verir; önce geri alıyoruz.
revoke update on public.devices from anon, authenticated;

-- Geriye tek yazılabilir sütun kalıyor: kullanıcının gerçekten sahip olduğu
-- tek alan, cihazın adı.
grant update (device_name) on public.devices to authenticated;

-- anon (giriş yapmamış) rolü hiçbir sütunu güncelleyemez — grant verilmedi.
-- Not: service_role bu revoke'tan etkilenmez, RLS ve kolon yetkilerini bypass eder.


-- -----------------------------------------------------------------------------
-- 4) metrics / logs / crash_snapshots — SADECE OKUMA
-- -----------------------------------------------------------------------------
-- Bu üç tabloda yalnızca SELECT politikası var. INSERT/UPDATE/DELETE politikası
-- tanımlanmadığı için RLS bu işlemleri user JWT ile tamamen reddeder — ayrıca
-- REVOKE yazmaya gerek yok, politikanın yokluğu zaten "reddet" demek.
-- Telemetri değiştirilemez olmalı: kullanıcı kendi geçmişini düzeltebilseydi
-- kara kutunun adli değeri kalmazdı.
-- Yazma tek yoldan gelir: collector, service key ile.
create policy sel_metrics on public.metrics
  for select
  using (account_id = (select auth.uid()));

create policy sel_logs on public.logs
  for select
  using (account_id = (select auth.uid()));

create policy sel_crash on public.crash_snapshots
  for select
  using (account_id = (select auth.uid()));

-- Retention silmelerini pg_cron postgres rolüyle yapar (RLS dışı) — DELETE
-- politikası olmaması retention'ı engellemez.


-- -----------------------------------------------------------------------------
-- 5) commands — oku + kendi cihazına komut ekle
-- -----------------------------------------------------------------------------
create policy sel_commands on public.commands
  for select
  using (account_id = (select auth.uid()));

-- INSERT — İKİ AYRI KONTROL, ikisi de zorunlu:
--
--   (a) account_id = auth.uid()
--       Kullanıcı satırı başkasının hesabına yazamasın.
--
--   (b) device_id GERÇEKTEN çağıranın bir cihazı mı?
--       Bu şart olmadan ciddi bir açık kalırdı: saldırgan KENDİ account_id'siyle
--       ama KURBANIN device_id'siyle satır ekleyebilirdi. (a) kontrolünden geçerdi.
--       Collector GET /commands'ı cihaz anahtarından çözdüğü device_id ile
--       sorguladığı için (account_id filtresi yok — CLAUDE.md §11 / Boşluk A),
--       komut kurbanın agent'ına teslim edilirdi. type='delete' ile bu, herhangi
--       bir kullanıcının başkasının makinesini uzaktan sildirmesi demekti.
--       exists(...) sorgusu devices'ın primary key'i üzerinden tek satır okur;
--       maliyeti ihmal edilebilir.
create policy ins_commands on public.commands
  for insert
  with check (
    account_id = (select auth.uid())
    and exists (
      select 1
      from public.devices d
      where d.id = device_id
        and d.account_id = (select auth.uid())
    )
  );

-- UPDATE politikası YOK: pending -> applied geçişini yalnızca collector yapar
-- (agent'ın ack'i üzerine). Kullanıcı bir komutu "uygulanmış" işaretleyebilseydi
-- agent onu hiç görmezdi.
-- DELETE politikası YOK: komut geçmişi denetim kaydıdır; silinmesi cihaz
-- silinmesiyle CASCADE üzerinden olur.


-- -----------------------------------------------------------------------------
-- DOĞRULAMA (Supabase Dashboard):
--   1. Table Editor -> 6 tablonun da yanında yeşil "RLS enabled" rozeti olmalı.
--   2. Advisors -> Security -> "RLS disabled in public" uyarısı KALMAMALI.
--   3. Gerçek test (M9, dashboard hazır olunca): iki test kullanıcısı aç,
--      birinin oturumuyla diğerinin cihazlarını sorgula -> 0 satır dönmeli.
-- -----------------------------------------------------------------------------

-- =============================================================================
-- TraceBox — db/migrations/0002_commands_insert_grant.sql
--
-- NE: `commands` tablosunda `authenticated` rolünün INSERT yetkisi üç sütuna
--     daraltılır: device_id, account_id, type.
--
-- NEDEN: RLS politikası (ins_commands) satırın KİME ait olduğunu doğruluyordu
--     ama hangi SÜTUNLARIN doldurulabileceğini söylemiyordu. Kullanıcı komutu
--     eklerken `status`'ü de kendisi yazabilirdi:
--
--       insert into commands (device_id, account_id, type, status)
--       values (..., 'delete', 'applied');
--
--     Agent yalnızca status='pending' olan komutları çeker → bu komut ona HİÇ
--     ulaşmaz. Ama dashboard'da "uygulandı" görünür. Kullanıcı cihazını
--     sildiğini sanır, cihaz çalışmaya devam eder. Komut geçmişi bir denetim
--     kaydıdır; yanıltıcı olması tek başına sorundur.
--     Bulgu: security_bugs.md → B7.
--
-- TARİH: 2026-08-21 — canlı Supabase'de aynı gün elle çalıştırıldı.
-- =============================================================================

revoke insert on public.commands from anon, authenticated;
grant  insert (device_id, account_id, type) on public.commands to authenticated;

-- =============================================================================
-- DOĞRULAMA — tam 3 satır dönmeli, üçü de authenticated:
--   account_id · device_id · type      (anon hiç görünmemeli)
-- =============================================================================
select grantee, column_name
  from information_schema.column_privileges
 where table_schema   = 'public'
   and table_name     = 'commands'
   and privilege_type = 'INSERT'
   and grantee in ('anon', 'authenticated')
 order by grantee, column_name;

-- -----------------------------------------------------------------------------
-- GERİ ALMA (çalıştırılmaz — sadece kayıt):
--   revoke insert (device_id, account_id, type) on public.commands from authenticated;
--   grant  insert on public.commands to authenticated;
-- UYARI: bu, B7'yi tekrar açar — kullanıcı yine status='applied' yazabilir hale gelir.
-- -----------------------------------------------------------------------------

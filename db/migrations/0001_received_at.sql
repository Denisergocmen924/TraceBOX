-- =============================================================================
-- TraceBox — db/migrations/0001_received_at.sql
--
-- NE: metrics, logs ve crash_snapshots tablolarına `received_at` sütunu ve
--     (account_id, received_at) indeksleri eklenir.
--
-- NEDEN: retention (gece çalışan silme işi) satırın yaşını `measured_at`'e
--     bakarak ölçüyordu. O alanı CİHAZ doldurur — yani verinin silinip
--     silinmeyeceğine, verinin sahibi karar veriyordu. Saatini ileri almış
--     bir cihazın verisi asla "eski" olmaz, sonsuza kadar birikirdi.
--     `received_at`'i SUNUCU (collector) yazar; retention artık ona bakar.
--     Bulgu: security_bugs.md → B5.
--
-- TARİH: 2026-08-21 — canlı Supabase'de aynı gün elle çalıştırıldı. Bu dosya
--     o değişikliğin kayda geçmiş halidir; yeni bir ortam bundan kurulur.
-- =============================================================================

alter table metrics         add column if not exists received_at timestamptz not null default now();
alter table logs            add column if not exists received_at timestamptz not null default now();
alter table crash_snapshots add column if not exists received_at timestamptz not null default now();

create index if not exists metrics_account_received_idx         on metrics         (account_id, received_at);
create index if not exists logs_account_received_idx            on logs            (account_id, received_at);
create index if not exists crash_snapshots_account_received_idx on crash_snapshots (account_id, received_at);


-- =============================================================================
-- DOĞRULAMA — 6 satır dönmeli: 3 sütun + 3 indeks.
-- `if not exists` yalnızca İSME bakar; bu sorgu ŞEKLE bakar.
-- =============================================================================
select 'sütun' as tur,
       table_name as tablo,
       data_type || ' · null? ' || is_nullable as ayrinti
  from information_schema.columns
 where table_schema = 'public' and column_name = 'received_at'
union all
select 'indeks', tablename, indexname
  from pg_indexes
 where schemaname = 'public' and indexname like '%account_received_idx'
 order by 1, 2;
-- Sütun satırlarında beklenen: timestamp with time zone · null? NO

-- -----------------------------------------------------------------------------
-- GERİ ALMA (çalıştırılmaz — sadece kayıt):
--   drop index if exists metrics_account_received_idx;
--   drop index if exists logs_account_received_idx;
--   drop index if exists crash_snapshots_account_received_idx;
--   alter table metrics         drop column if exists received_at;
--   alter table logs            drop column if exists received_at;
--   alter table crash_snapshots drop column if exists received_at;
--
-- UYARI: sütunu düşürmek içindeki veriyi de siler, geri gelmez. Ayrıca
-- retention yeniden measured_at'e döner — yani B5 açığı tekrar açılır.
-- -----------------------------------------------------------------------------

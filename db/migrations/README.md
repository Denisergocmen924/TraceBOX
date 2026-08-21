# db/migrations/

Veritabanına yapılan her değişikliğin **yazılı, çalıştırılabilir kaydı**.

## Neden var

`schema.sql` tabloların **son halini** anlatır — sıfırdan kurulum için. Ama içinde veri olan
bir veritabanına `schema.sql` çalıştırılamaz: `create table` "zaten var" der, `drop table`
ise satırları siler. Zaten kurulmuş bir veritabanını değiştirmenin tek güvenli yolu
`alter table`'dır. Migration dosyaları o `alter`'ları saklar.

Veritabanı, projedeki **silinip baştan kurulamayan tek şeydir**. Kod, container, Fly makinesi —
hepsi dosyadan geri gelir. Veri gelmez.

## Kural (2026-08-21)

**Her veritabanı değişikliğinde İKİ dosya birden güncellenir:**

1. Yeni bir `NNNN_kisa_ad.sql` migration — "şöyleyken böyle oldu"
2. `schema.sql` (veya `rls.sql` / `triggers.sql`) — "şu an böyle"

Tekrar bilerek kabul edildi: "tablolar neye benziyor" sorusunun cevabı tek dosyada kalsın diye.
Bedeli, birini güncelleyip diğerini unutma riskidir — bu yüzden kural burada yazılı.

## Yazım kuralları

- **Ad:** `NNNN_kisa_ad.sql`. Numara dört haneli ve sıfır dolgulu (`0001`, `0010`) — alfabetik
  sıralama sayısal sırayla aynı olsun diye (`1, 10, 2` sorunu).
- **Sıra:** dosyalar numara sırasıyla çalıştırılır. Sonraki migration öncekinin bıraktığı
  durumu varsayabilir.
- **Alakasız işler ayrı dosyalara.** Birini geri almak diğerine dokunmasın.
- **`if not exists` kullanılır** (`alter table ... add column if not exists`,
  `create index if not exists`). Elle çalıştırılan bir akış bu; "acaba çalıştırmış mıydım"
  sorusu kaçınılmaz, `if not exists` onu zararsız kılar.
  Zaafı: yalnızca **isme** bakar, şekle bakmaz. Bu yüzden ↓
- **Her dosyanın sonunda DOĞRULAMA sorgusu bulunur** — sonucun şeklini (`data_type`,
  `is_nullable`, yetkiler) okur. Sessizliğe güvenilmez.
- **GERİ ALMA yorum içinde yazılır**, çalıştırılabilir olarak değil. Bilgi kaybolmasın ama
  kazayla tetiklenmesin.
- **Başlıkta NE / NEDEN / TARİH bulunur.** `alter table` satırını herkes okur; "neden"in
  cevabı yalnızca yazanın kafasındadır.

## Uygulanmış migration'lar

| # | Dosya | Konu | Canlıda |
|---|-------|------|---------|
| 0001 | `0001_received_at.sql` | `received_at` sütunu + indeksler (bulgu B5) | ✅ 2026-08-21 |
| 0002 | `0002_commands_insert_grant.sql` | `commands` INSERT yetkisi daraltıldı (bulgu B7) | ✅ 2026-08-21 |

> Her ikisi de canlı Supabase'de **elle** çalıştırıldı; dosyalar sonradan kayda geçirildi.
> Yeni bir ortam kurulursa: `schema.sql` → `triggers.sql` → `rls.sql`, migration'lara gerek yok
> (değişiklikler zaten o dosyalara işlendi). Migration'lar **mevcut** bir veritabanı içindir.

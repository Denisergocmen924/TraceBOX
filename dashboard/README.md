# Dashboard — M9'da gelecek

Next.js + Tailwind. Kullanıcının **okuma** penceresi.

Bu klasör M9'a kadar bilerek boş. Sebep: dikey dilim yaklaşımı. Önce veri
gerçekten Supabase'e düşsün (M3), sonra onu gösteren arayüz yazılsın. Tersini
yapmak, henüz var olmayan verinin sahte kopyasına karşı UI geliştirmek olurdu.

## Kurulacak mimari

- **Okuma:** Supabase client ile **doğrudan** Postgres'ten. Collector'a hiçbir
  okuma isteği gitmez — RLS (`account_id = auth.uid()`) zaten satır bazında
  koruyor, araya bir API katmanı koymak fayda getirmeden gecikme eklerdi.
- **Auth:** Supabase Auth (e-posta/şifre).
- **Yazma:**
  - "Cihaz Ekle" → collector `POST /devices` (user JWT) → anahtar **bir kez**
    gösterilir. Bu tek yazma işlemi collector'dan geçer, çünkü anahtar üretimi
    ve hash'leme tarayıcıda yapılamaz.
  - pause / resume / delete → `commands` tablosuna doğrudan INSERT
    (RLS `ins_commands` politikası cihaz sahipliğini de doğrular).
- **Barındırma:** Vercel.

## Gösterilecekler

- Cihaz kartları (CPU / RAM / disk anlık durum)
- 10 günlük zaman çizelgesi (metrik + log birlikte)
- `last_seen` üzerinden offline rozeti
- pause / resume / delete butonları
- Log seviye filtresi (info / warning / error / critical)

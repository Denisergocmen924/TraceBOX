-- =============================================================================
-- BlackBox — db/triggers.sql
-- auth.users -> accounts otomatik satır açma.
--
-- ÇALIŞTIRMA SIRASI:  schema.sql  ->  [triggers.sql]  ->  rls.sql
--
-- ÇÖZÜLEN SORUN:
--   Supabase Auth kullanıcıyı auth.users içinde oluşturur; bizim accounts
--   tablomuzdan haberi yoktur. Bu satırı uygulama kodunda açsaydık ("kayıt
--   sonrası bir de accounts insert et") her kayıt yolu (e-posta, OAuth, davet,
--   dashboard'dan manuel ekleme) bu adımı ayrı ayrı hatırlamak zorunda kalırdı;
--   biri unutulunca kullanıcı hesapsız kalır ve RLS yüzünden HİÇBİR ŞEY göremez.
--   Trigger, kuralı veritabanına gömer: kayıt yolu ne olursa olsun satır açılır.
-- =============================================================================


create or replace function public.handle_new_user()
returns trigger
language plpgsql
-- SECURITY DEFINER: fonksiyon, çağıranın değil, SAHİBİNİN yetkileriyle çalışır.
-- Zorunlu, çünkü kaydı tetikleyen taraf henüz kimliği doğrulanmamış yeni bir
-- kullanıcıdır ve accounts tablosuna yazma yetkisi yoktur.
security definer
-- search_path'i boşaltmak SECURITY DEFINER fonksiyonlarda zorunlu bir sertleştirme:
-- boş bırakılırsa saldırgan kendi şemasına sahte bir "accounts" tablosu koyup
-- arama yolunu kaçırarak bu fonksiyonu yetkileriyle kandırabilir. Boş search_path
-- ile her nesne adını tam nitelemek (public.accounts) zorunlu hale gelir.
-- Supabase güvenlik linter'ı da bunu ister.
set search_path = ''
as $$
begin
  insert into public.accounts (id) values (new.id)
  -- Savunma amaçlı: satır bir şekilde zaten varsa (ör. veri taşıma, trigger'ın
  -- iki kez kurulması) kayıt işlemini hata ile düşürmeyelim. Hesabın var olması
  -- amaçlanan sonuç; nasıl oluştuğu önemli değil.
  on conflict (id) do nothing;

  -- AFTER trigger'da dönüş değeri yok sayılır ama plpgsql yine de zorunlu tutar.
  return new;
end;
$$;


-- Trigger'ı yeniden çalıştırılabilir yapmak için önce düşürüyoruz:
-- "create trigger" IF NOT EXISTS desteklemez, bu dosyayı ikinci kez çalıştırmak
-- aksi halde hata verir.
drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
  -- AFTER: kullanıcı auth.users'a başarıyla yazıldıktan SONRA. BEFORE olsaydı,
  -- kayıt daha sonra başarısız olduğunda ortada sahibi olmayan bir accounts
  -- satırı kalırdı.
  after insert on auth.users
  for each row execute function public.handle_new_user();


-- -----------------------------------------------------------------------------
-- DOĞRULAMA (Supabase Dashboard):
--   Authentication -> Users -> Add user ile bir test kullanıcısı oluştur.
--   Table Editor -> accounts tablosunda AYNI UUID ile bir satır belirmeli.
--   Belirmediyse trigger kurulmamıştır; bu dosyayı tekrar çalıştır.
-- -----------------------------------------------------------------------------

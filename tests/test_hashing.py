"""collector/hashing.py — cihaz anahtarının özetlenmesi ve karşılaştırılması."""

from hashing import hash_device_key, hashes_match


def test_hash_does_not_return_plain_key():
    """Özet anahtarın kendisi olamaz: veritabanında düz anahtar durmaz."""
    key = "tbx_live_ornek"
    assert hash_device_key(key) != key


def test_same_key_gives_same_hash():
    """Doğrulama buna dayanır: aynı anahtar her seferinde aynı özeti üretmeli."""
    assert hash_device_key("tbx_live_a") == hash_device_key("tbx_live_a")


def test_different_keys_give_different_hashes():
    """Farklı cihazlar farklı satırlara düşmeli."""
    assert hash_device_key("tbx_live_a") != hash_device_key("tbx_live_b")


def test_matching_hashes_are_accepted():
    """Doğru anahtar içeri girebilmeli — yoksa hiçbir cihaz bağlanamaz."""
    digest = hash_device_key("tbx_live_a")
    assert hashes_match(digest, digest) is True


def test_different_hashes_are_rejected():
    """Bu dosyadaki en kritik iddia.

    Karşılaştırma her koşulda True dönseydi hiçbir şey görünürde bozulmazdı:
    cihazlar çalışmaya devam eder, log temiz kalır, sağlık kontrolü yeşil
    yanar — ve rastgele bir anahtarla herkes içeri girerdi. Sessiz bir açık
    olduğu için tek bekçisi bu testtir.
    """
    assert hashes_match(hash_device_key("tbx_live_a"), hash_device_key("tbx_live_b")) is False


def test_wrong_key_does_not_match_stored_hash():
    """Doğrulamanın uçtan uca anlamı: yanlış anahtar kayıtlı özeti tutturamaz."""
    stored = hash_device_key("tbx_live_dogru")
    assert hashes_match(hash_device_key("tbx_live_yanlis"), stored) is False

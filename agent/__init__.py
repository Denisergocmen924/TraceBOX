"""
TraceBox Agent — izlenen makinede çalışan toplayıcı.

Görevi: metrik ve sistem loglarını sürekli toplamak, diske (spool) yazmak ve
makine çökmeden ÖNCE collector'a göndermek. Projenin ana içgörüsü burada:
çöküş anında göndermeye çalışmak çok geç olur, bu yüzden veri sürekli dışarı
taşınır (CLAUDE.md §0).

PAKET YAPISI:
    core/        Çekirdek — döngü, config, state, toplama, spool, gönderim.
                 İşletim sistemine özgü hiçbir şey bilmez.
    logsources/  OS'a özgü log okuyucular. Her biri LogSource sözleşmesini
                 uygular ve kendi çıktısını ortak LogRecord şekline çevirir.
                 Bağımlılık izolasyonu (CLAUDE.md §2): journald'ı yalnızca bu
                 klasör tanır; Windows desteği gerektiğinde çekirdek kodun tek
                 satırı bile değişmez.

ÇALIŞTIRMA (M1'de gelecek):
    python -m agent
    systemd unit'i tam olarak bunu çağırır (CLAUDE.md §8, adım 5), bu yüzden
    paketin bir __main__.py'si olacak.
"""

# POST /inventory ile sunucuya bildirilir ve devices.agent_version sütununa
# yazılır. Bir cihazın hangi agent sürümünü çalıştırdığını bilmek, sürüme özgü
# bir hatayı teşhis etmenin tek yoludur.
#
# 0.x = MVP, sözleşmeler henüz değişebilir. Wire payload'ları (CLAUDE.md §4.2)
# dondurulduğunda 1.0.0'a çıkılacak.
__version__ = "0.1.0"

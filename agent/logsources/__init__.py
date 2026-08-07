"""
agent.logsources — işletim sistemine özgü log okuyucular.

SÖZLEŞME (CLAUDE.md §4.1): her okuyucu LogSource soyut sınıfını uygular ve
kendi OS'unun çıktısını sabit LogRecord şekline normalize eder:

    {timestamp, level, message, source}

level yalnızca 4 değer alır: info | warning | error | critical.
journald'ın 8 PRIORITY seviyesi bu 4'e burada indirgenir; çekirdek kod ham
seviyeleri hiç görmez.

Bu klasörün varlık sebebi: journald'ı tüm koda yaymamak. Windows desteği
gerektiğinde yapılacak tek iş buraya bir windows_eventlog.py eklemek olacak —
agent.core'da tek satır değişmeyecek.

MODÜLLER:
    base.py             M4  LogSource ABC + LogRecord dataclass (SÖZLEŞME).
    linux_journald.py   M4  İlk implementasyon. journald cursor'ını kullanır;
                            cursor state.json'da saklanır, böylece agent yeniden
                            başladığında kaldığı yerden devam eder ve ne log
                            kaybeder ne de tekrarlar.
"""

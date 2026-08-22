"""agent/core/metrics.py — ölçüm toplayıcısının ilk çağrı davranışı."""

from agent.core.metrics import MetricsCollector


def test_first_sample_has_no_network_rate():
    """İlk ölçümde karşılaştırılacak önceki sayaç yoktur.

    Hız hesaplanamadığında 0.0 değil None yazılır: 0.0 "trafik yoktu"
    demek olurdu, oysa burada ölçüm hiç yapılamamıştır.
    """
    sample = MetricsCollector().collect()
    assert sample.net_sent_mb is None
    assert sample.net_recv_mb is None

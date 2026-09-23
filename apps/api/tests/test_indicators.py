from app.engine.indicators import ema, rsi, slope


def test_ema_and_slope_uptrend():
    values = [100 + i for i in range(30)]
    assert ema(values, 8) is not None
    assert ema(values, 8) > ema(values, 20)
    assert slope(values, 5) > 0


def test_rsi_strong_uptrend():
    values = [100 + i for i in range(20)]
    value = rsi(values, 14)
    assert value is not None
    assert value > 70

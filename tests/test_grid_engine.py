from decimal import Decimal as D
import pytest
from crypto_signal_engine.grid.engine import GridConfig, GridEngine

def config(**kwargs):
    values = dict(lower=D("90"), upper=D("110"), intervals=4,
                  capital=D("1000"), initial_price=D("100"))
    values.update(kwargs)
    return GridConfig(**values)

def test_arithmetic_levels():
    engine = GridEngine(config())
    assert engine.levels == list(map(D, ["90", "95", "100", "105", "110"]))

def test_buy_and_sell_cycle():
    engine = GridEngine(config())
    engine.on_price(D("94"))
    assert any(f["side"] == "BUY" for f in engine.fills)
    engine.on_price(D("102"))
    assert any(f["side"] == "SELL" for f in engine.fills)
    assert D(engine.snapshot()["fees_usdt"]) > 0

def test_no_fill_on_unchanged_price():
    engine = GridEngine(config())
    engine.on_price(D("100"))
    assert engine.fills == []

def test_geometric_levels():
    engine = GridEngine(config(mode="geometric"))
    assert len(engine.levels) == 5
    assert engine.levels == sorted(engine.levels)

def test_invalid_range():
    with pytest.raises(ValueError):
        GridEngine(config(lower=D("120")))

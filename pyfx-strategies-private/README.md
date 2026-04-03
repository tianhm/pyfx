# pyfx Private Strategies

Private trading strategies for pyfx. This repo is separate from the public pyfx repo.

## Setup

```bash
# Install pyfx first (from the main repo)
uv pip install -e /path/to/pyfx-cli

# Install private strategies
uv pip install -e .
```

## Usage

Once installed, strategies are automatically discovered by pyfx:

```bash
# List all strategies (public + private)
pyfx strategies

# Run a backtest with a private strategy
pyfx backtest -s rsi_trend -i EUR/USD \
  --start 2023-01-01 --end 2023-12-31 \
  --data-file ~/.pyfx/data/EURUSD_365d_M1.parquet \
  --save
```

## Adding a New Strategy

1. Create a new file in `strategies/` (e.g. `strategies/my_strategy.py`)
2. Extend `PyfxStrategy` and `PyfxStrategyConfig` from `pyfx.strategies.base`
3. Register it in `pyproject.toml` under `[project.entry-points."pyfx.strategies"]`
4. Run `uv pip install -e .` to update the entry points

```python
from decimal import Decimal
from nautilus_trader.model.data import Bar
from pyfx.strategies.base import PyfxStrategy, PyfxStrategyConfig

class MyConfig(PyfxStrategyConfig, frozen=True):
    my_param: int = 20
    trade_size: Decimal = Decimal("100000")

class MyStrategy(PyfxStrategy):
    def __init__(self, config: MyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        # Your logic here
        pass
```

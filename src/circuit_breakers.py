"""Circuit breakers del bot: funciones puras (estado) -> bool.

Cada uno se testea sin tocar Polymarket. Ver docs/RISK_CONTROLS.md #4.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskLimits:
    max_daily_drawdown_pct: float = 0.05
    max_session_drawdown_pct: float = 0.15
    max_consecutive_losses: int = 5
    max_stale_seconds: float = 30.0
    max_api_errors_in_window: int = 3
    api_error_window_seconds: float = 60.0
    max_slippage_pct: float = 0.03


@dataclass
class BotState:
    bankroll_start_of_day: float
    bankroll_peak: float
    bankroll_now: float
    now_ts: float
    last_price_update_ts: float
    recent_outcomes: list = field(default_factory=list)  # bool, mas reciente al final
    recent_api_errors_ts: list = field(default_factory=list)
    expected_price: float = None
    executed_price: float = None


def daily_drawdown_breaker(state, limits):
    if state.bankroll_start_of_day <= 0:
        return False
    dd = (state.bankroll_start_of_day - state.bankroll_now) / state.bankroll_start_of_day
    return dd >= limits.max_daily_drawdown_pct


def session_drawdown_breaker(state, limits):
    if state.bankroll_peak <= 0:
        return False
    dd = (state.bankroll_peak - state.bankroll_now) / state.bankroll_peak
    return dd >= limits.max_session_drawdown_pct


def losing_streak_breaker(state, limits):
    if len(state.recent_outcomes) < limits.max_consecutive_losses:
        return False
    tail = state.recent_outcomes[-limits.max_consecutive_losses:]
    return all(outcome is False for outcome in tail)


def stale_data_breaker(state, limits):
    return (state.now_ts - state.last_price_update_ts) >= limits.max_stale_seconds


def api_error_breaker(state, limits):
    window_start = state.now_ts - limits.api_error_window_seconds
    recent = [t for t in state.recent_api_errors_ts if t >= window_start]
    return len(recent) >= limits.max_api_errors_in_window


def slippage_breaker(state, limits):
    if state.expected_price is None or state.executed_price is None:
        return False
    if state.expected_price <= 0:
        return False
    deviation = abs(state.executed_price - state.expected_price) / state.expected_price
    return deviation >= limits.max_slippage_pct


BREAKERS = {
    "drawdown_diario": daily_drawdown_breaker,
    "drawdown_sesion": session_drawdown_breaker,
    "racha_perdidas": losing_streak_breaker,
    "datos_stale": stale_data_breaker,
    "errores_api": api_error_breaker,
    "slippage_anomalo": slippage_breaker,
}


def check_breakers(state, limits=None):
    """Nombres de los breakers activados. Lista vacia = ok para operar."""
    limits = limits or RiskLimits()
    return [name for name, fn in BREAKERS.items() if fn(state, limits)]


def _demo():
    limits = RiskLimits(
        max_daily_drawdown_pct=0.05,
        max_session_drawdown_pct=0.15,
        max_consecutive_losses=3,
        max_stale_seconds=30,
        max_api_errors_in_window=3,
        api_error_window_seconds=60,
        max_slippage_pct=0.03,
    )

    ok = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=990,
        now_ts=1000, last_price_update_ts=995,
        recent_outcomes=[True, False, True],
        recent_api_errors_ts=[],
    )
    assert check_breakers(ok, limits) == []

    dd_daily = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=940,
        now_ts=1000, last_price_update_ts=995,
    )
    assert check_breakers(dd_daily, limits) == ["drawdown_diario"]

    dd_session = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1200, bankroll_now=1000,
        now_ts=1000, last_price_update_ts=995,
    )
    assert check_breakers(dd_session, limits) == ["drawdown_sesion"]

    racha = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=1000,
        now_ts=1000, last_price_update_ts=995,
        recent_outcomes=[True, False, False, False],
    )
    assert check_breakers(racha, limits) == ["racha_perdidas"]

    stale = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=1000,
        now_ts=1000, last_price_update_ts=900,
    )
    assert check_breakers(stale, limits) == ["datos_stale"]

    errores = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=1000,
        now_ts=1000, last_price_update_ts=995,
        recent_api_errors_ts=[950, 960, 970],
    )
    assert check_breakers(errores, limits) == ["errores_api"]

    slip = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=1000,
        now_ts=1000, last_price_update_ts=995,
        expected_price=0.50, executed_price=0.53,
    )
    assert check_breakers(slip, limits) == ["slippage_anomalo"]

    combinado = BotState(
        bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=940,
        now_ts=1000, last_price_update_ts=900,
    )
    assert set(check_breakers(combinado, limits)) == {"drawdown_diario", "datos_stale"}

    print("circuit_breakers autotest OK (7 escenarios)")


if __name__ == "__main__":
    _demo()

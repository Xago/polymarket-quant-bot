"""Persiste el bankroll, el pico, y la racha reciente entre invocaciones.

Cada corrida de launchd es un proceso nuevo: sin esto, bankroll_now y
recent_outcomes arrancan de cero cada vez y los circuit breakers de
drawdown/racha (circuit_breakers.py) nunca tienen memoria real. Es el
mismo problema que positions.json ya resuelve para las posiciones
abiertas, aplicado al bankroll.
"""

import datetime
import json
import os

MAX_RECENT_OUTCOMES = 50


def load_state(path, bankroll0):
    if not os.path.exists(path):
        return _fresh_state(bankroll0)
    with open(path) as f:
        state = json.load(f)

    today = datetime.date.today().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["bankroll_start_of_day"] = state["bankroll_now"]
    return state


def _fresh_state(bankroll0):
    return {
        "day": datetime.date.today().isoformat(),
        "bankroll_start_of_day": bankroll0,
        "bankroll_peak": bankroll0,
        "bankroll_now": bankroll0,
        "recent_outcomes": [],
    }


def record_close(state, pnl):
    state["bankroll_now"] += pnl
    state["bankroll_peak"] = max(state["bankroll_peak"], state["bankroll_now"])
    state["recent_outcomes"].append(pnl > 0)
    state["recent_outcomes"] = state["recent_outcomes"][-MAX_RECENT_OUTCOMES:]


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def resume_after_breaker(state_path, log_path, bankroll0, reason):
    """Reactivacion manual explicita despues de un freno de racha de
    perdidas. Limpia recent_outcomes (el breaker deja de bloquear) y deja
    registro auditable de quien/cuando/por que se autorizo -- igual que
    kill_switch.activate()/deactivate(), esto NUNCA se llama solo desde
    el loop, es una decision humana. Ver RISK_CONTROLS.md #4 y #9.
    """
    state = load_state(state_path, bankroll0)
    state["recent_outcomes"] = []
    save_state(state_path, state)

    with open(log_path, "a") as f:
        f.write(json.dumps({
            "ts": datetime.datetime.now().timestamp(),
            "action": "RESUME",
            "reason": reason,
            "bankroll_now": round(state["bankroll_now"], 2),
        }) + "\n")

    return state


def _demo():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bankroll_state.json")

        # A: sin archivo previo -> estado fresco
        state = load_state(path, bankroll0=1000.0)
        assert state["bankroll_now"] == 1000.0
        assert state["recent_outcomes"] == []

        # B: se guarda y se puede volver a cargar, con memoria real
        record_close(state, pnl=5.0)
        record_close(state, pnl=-2.0)
        save_state(path, state)
        reloaded = load_state(path, bankroll0=1000.0)
        assert reloaded["bankroll_now"] == 1003.0
        assert reloaded["recent_outcomes"] == [True, False]
        assert reloaded["bankroll_peak"] == 1005.0

        # C: si cambia el dia, bankroll_start_of_day se resetea al valor
        # actual (no al bankroll0 original), pero el resto de la memoria
        # (racha, pico) se mantiene
        reloaded["day"] = "2000-01-01"
        save_state(path, reloaded)
        next_day = load_state(path, bankroll0=1000.0)
        assert next_day["bankroll_start_of_day"] == 1003.0
        assert next_day["recent_outcomes"] == [True, False]

        # D: resume_after_breaker apaga el freno de racha y deja registro
        from circuit_breakers import BotState, RiskLimits, losing_streak_breaker

        limits = RiskLimits(max_consecutive_losses=5)
        racha = load_state(path, bankroll0=1000.0)
        racha["recent_outcomes"] = [False] * 6  # 6 perdidas seguidas
        save_state(path, racha)
        antes = BotState(
            bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=1000,
            now_ts=0, last_price_update_ts=0, recent_outcomes=racha["recent_outcomes"],
        )
        assert losing_streak_breaker(antes, limits) is True

        log_path = os.path.join(d, "log.jsonl")
        resumed = resume_after_breaker(path, log_path, bankroll0=1000.0, reason="prueba: racha era ruido")
        despues = BotState(
            bankroll_start_of_day=1000, bankroll_peak=1000, bankroll_now=1000,
            now_ts=0, last_price_update_ts=0, recent_outcomes=resumed["recent_outcomes"],
        )
        assert losing_streak_breaker(despues, limits) is False

        with open(log_path) as f:
            entries = [json.loads(l) for l in f]
        assert entries[0]["action"] == "RESUME"
        assert entries[0]["reason"] == "prueba: racha era ruido"

    print("bankroll_state autotest OK (4 escenarios)")


if __name__ == "__main__":
    _demo()

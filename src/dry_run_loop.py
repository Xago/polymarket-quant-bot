"""Loop de dry-run: une sizing + circuit breakers + kill switch contra el
order book real y publico de Polymarket. Nunca firma ni envia una orden,
solo decide que haria y lo deja en un log append-only. Ver
docs/RISK_CONTROLS.md #7 y #9.

Uso real (contra Polymarket en vivo): python3 dry_run_loop.py
"""

import json
import time

import kill_switch
from bankroll_state import load_state, record_close, save_state
from circuit_breakers import BotState, RiskLimits, check_breakers
from market_data import book_spread, pick_active_markets, price_history, taker_fee_per_share
from position_tracker import load_ledger, open_position, resolve_position, save_ledger
from risk_sizing import kelly_fraction


def _log(log_path, entry):
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run(
    n_ticks=5,
    bankroll0=1000.0,
    sleep_seconds=2,
    n_markets=5,
    limits=None,
    log_path="dry_run_log.jsonl",
    positions_path="positions.json",
    bankroll_state_path="bankroll_state.json",
    position_timeout_seconds=1800.0,
    kill_switch_path=kill_switch.DEFAULT_PATH,
    list_markets=pick_active_markets,
    get_book=book_spread,
    get_history=price_history,
):
    limits = limits or RiskLimits()
    bankroll_st = load_state(bankroll_state_path, bankroll0)
    recent_api_errors_ts = []
    open_positions = load_ledger(positions_path)

    markets = None
    for tick in range(n_ticks):
        now_ts = time.time()

        if kill_switch.is_active(kill_switch_path):
            _log(log_path, {"ts": now_ts, "tick": tick, "action": "HALT", "reason": "kill_switch activo"})
            print(f"[tick {tick}] kill switch activo -> loop detenido")
            return

        # 1) resolver posiciones abiertas ANTES de evaluar entradas nuevas:
        # asi el bankroll y la racha reflejan resultados reales, no solo intenciones
        still_open = []
        for pos in open_positions:
            try:
                closed, pnl, reason = resolve_position(pos, get_history, now_ts)
            except Exception as e:
                recent_api_errors_ts.append(now_ts)
                still_open.append(pos)  # se reintenta en el proximo tick, no se pierde
                _log(log_path, {"ts": now_ts, "tick": tick, "market": pos.slug, "action": "SKIP", "reason": f"error API: {e}"})
                continue
            if not closed:
                still_open.append(pos)
                continue
            record_close(bankroll_st, pnl)
            _log(log_path, {
                "ts": now_ts, "tick": tick, "market": pos.slug, "action": "CLOSE",
                "reason": reason, "pnl": round(pnl, 4), "bankroll_now": round(bankroll_st["bankroll_now"], 2),
            })
            print(f"[tick {tick}] {pos.slug}: cerro ({reason}), pnl ${pnl:.2f}, bankroll ${bankroll_st['bankroll_now']:.2f}")
        open_positions = still_open
        save_ledger(positions_path, open_positions)
        save_state(bankroll_state_path, bankroll_st)

        if markets is None:
            markets = list_markets(n_markets)

        open_slugs = {p.slug for p in open_positions}
        for slug, token_id, _vol, fee_rate in markets:
            if slug in open_slugs:
                continue  # ya hay una posicion abierta en este mercado, no duplicar

            try:
                result = get_book(token_id)
            except Exception as e:
                recent_api_errors_ts.append(now_ts)
                _log(log_path, {"ts": now_ts, "tick": tick, "market": slug, "action": "SKIP", "reason": f"error API: {e}"})
                continue
            if result is None:
                continue
            best_bid, best_ask, _bid_size, _ask_size = result
            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            net_worst = spread - taker_fee_per_share(mid, fee_rate)

            state = BotState(
                bankroll_start_of_day=bankroll_st["bankroll_start_of_day"],
                bankroll_peak=bankroll_st["bankroll_peak"],
                bankroll_now=bankroll_st["bankroll_now"],
                now_ts=now_ts,
                last_price_update_ts=now_ts,  # dato recien pedido en este mismo tick
                recent_outcomes=bankroll_st["recent_outcomes"],
                recent_api_errors_ts=recent_api_errors_ts,
            )
            triggered = check_breakers(state, limits)
            if triggered:
                _log(log_path, {"ts": now_ts, "tick": tick, "market": slug, "action": "SKIP", "reason": triggered})
                continue

            f = kelly_fraction(p_hat=best_bid + max(net_worst, 0), price=best_bid)
            dollars = f * bankroll_st["bankroll_now"]
            if dollars <= 0:
                _log(log_path, {"ts": now_ts, "tick": tick, "market": slug, "action": "SKIP", "reason": "sin edge neto positivo"})
                continue

            pos = open_position(
                slug, token_id, entry_price=best_bid, exit_target=best_ask,
                size_usd=dollars, edge_per_share=net_worst, opened_ts=now_ts,
                timeout_seconds=position_timeout_seconds,
            )
            open_positions.append(pos)
            save_ledger(positions_path, open_positions)

            _log(log_path, {
                "ts": now_ts, "tick": tick, "market": slug, "action": "DRY_RUN_ENTRY",
                "best_bid": best_bid, "best_ask": best_ask, "net_edge": round(net_worst, 5),
                "size_usd": round(dollars, 2), "bankroll_now": round(bankroll_st["bankroll_now"], 2),
            })
            print(f"[tick {tick}] {slug}: entraria (simulado) con ${dollars:.2f} (edge neto {net_worst:.4f})")

        if tick < n_ticks - 1:
            time.sleep(sleep_seconds)

    print(f"loop terminado ({n_ticks} ticks), log en {log_path}, posiciones abiertas: {len(open_positions)}")


def _demo():
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "log.jsonl")
        kill_path = os.path.join(d, "KILL_SWITCH")
        pos_path = os.path.join(d, "positions.json")
        bank_path = os.path.join(d, "bankroll_state.json")

        # 1) kill switch activo desde el inicio -> el loop no debe ni
        #    intentar pedir mercados (list_markets explota si se llama)
        def _boom(*a, **kw):
            raise AssertionError("no deberia llamarse con kill switch activo")

        kill_switch.activate("prueba", kill_path)
        run(n_ticks=3, log_path=log_path, positions_path=pos_path, bankroll_state_path=bank_path,
            kill_switch_path=kill_path, list_markets=_boom, get_book=_boom)
        with open(log_path) as f:
            lines = [json.loads(l) for l in f]
        assert len(lines) == 1 and lines[0]["action"] == "HALT"
        kill_switch.deactivate(kill_path)
        os.remove(log_path)

        # 2) spread real positivo, sin comision (fee_rate=0) -> debe entrar
        #    y quedar registrada como posicion abierta en el ledger
        fake_markets = [("mercado-test", "tok1", 0, 0.0)]
        fake_book = {"tok1": (0.40, 0.42, 100, 100)}
        run(n_ticks=1, log_path=log_path, positions_path=pos_path, bankroll_state_path=bank_path,
            kill_switch_path=kill_path, list_markets=lambda n: fake_markets,
            get_book=lambda tid: fake_book[tid])
        with open(log_path) as f:
            lines = [json.loads(l) for l in f]
        assert lines[0]["action"] == "DRY_RUN_ENTRY", lines
        assert lines[0]["size_usd"] > 0
        assert len(load_ledger(pos_path)) == 1
        os.remove(log_path)
        os.remove(pos_path)

        # 3) mismo spread, pero comision alta se come el margen -> debe
        #    saltear, no entrar, no abrir posicion
        fake_markets_fee = [("mercado-test-fee", "tok2", 0, 0.9)]
        fake_book_fee = {"tok2": (0.40, 0.42, 100, 100)}
        run(n_ticks=1, log_path=log_path, positions_path=pos_path, bankroll_state_path=bank_path,
            kill_switch_path=kill_path, list_markets=lambda n: fake_markets_fee,
            get_book=lambda tid: fake_book_fee[tid])
        with open(log_path) as f:
            lines = [json.loads(l) for l in f]
        assert lines[0]["action"] == "SKIP", lines
        assert lines[0]["reason"] == "sin edge neto positivo"
        assert load_ledger(pos_path) == []
        os.remove(log_path)
        os.remove(pos_path)

        # 4) una posicion ya abierta cuyo precio de salida SI fue tocado
        #    -> se cierra con ganancia real y el bankroll sube
        preexistente = open_position(
            "m-preexist", "tok-preexist", entry_price=0.40, exit_target=0.42,
            size_usd=10.0, edge_per_share=0.02, opened_ts=1_700_000_000, timeout_seconds=3600,
        )
        save_ledger(pos_path, [preexistente])
        run(n_ticks=1, bankroll0=1000.0, log_path=log_path, positions_path=pos_path,
            bankroll_state_path=bank_path, kill_switch_path=kill_path, list_markets=lambda n: [],
            get_history=lambda tid, s, e: [(s, 0.40), (s + 50, 0.43)])
        with open(log_path) as f:
            lines = [json.loads(l) for l in f]
        assert lines[0]["action"] == "CLOSE" and lines[0]["reason"] == "spread_capturado"
        pnl_esperado = (10.0 / 0.40) * 0.02
        assert abs(lines[0]["pnl"] - pnl_esperado) < 1e-6
        assert abs(lines[0]["bankroll_now"] - (1000.0 + pnl_esperado)) < 1e-6
        assert load_ledger(pos_path) == []

    print("dry_run_loop autotest OK (4 escenarios, sin red)")


if __name__ == "__main__":
    _demo()
    print("\n--- ahora una corrida real y breve contra Polymarket en vivo ---")
    run(n_ticks=2, sleep_seconds=1, n_markets=5, log_path="dry_run_log.jsonl")

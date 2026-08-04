"""Kelly fraccional conservador para mercados binarios (tipo Polymarket).

f = min(hard_cap, kelly_frac * (shrink * (p_hat - price)) / (1 - price))

Ver docs/RISK_CONTROLS.md para el contexto de las reglas de position sizing.
"""

import random
import statistics


def kelly_fraction(p_hat, price, shrink=0.5, kelly_frac=0.25, hard_cap=0.02):
    """Fracción del bankroll a apostar en un contrato binario a precio `price`."""
    if not (0 < price < 1):
        raise ValueError("price debe estar entre 0 y 1")
    edge = shrink * (p_hat - price)
    if edge <= 0:
        return 0.0
    full_kelly = edge / (1 - price)
    return min(hard_cap, kelly_frac * full_kelly)


def simulate(sizing_fn, n_trades=2000, bankroll0=1000.0, seed=0):
    """Corre la misma secuencia de trades con una función de sizing dada.

    Devuelve (bankroll_final, max_drawdown, ruina) donde ruina = True si
    el bankroll cae por debajo del 10% del inicial en algún punto.
    """
    rng = random.Random(seed)
    bankroll = bankroll0
    peak = bankroll0
    max_dd = 0.0
    ruina = False

    for _ in range(n_trades):
        price = rng.uniform(0.3, 0.7)
        true_edge = rng.gauss(0.02, 0.03)          # edge real, promedio positivo pero ruidoso
        true_p = min(0.99, max(0.01, price + true_edge))
        estimation_noise = rng.gauss(0, 0.03)       # el bot no conoce true_edge exacto
        p_hat = min(0.99, max(0.01, price + true_edge + estimation_noise))

        f = sizing_fn(p_hat, price)
        if f <= 0:
            continue

        win = rng.random() < true_p
        if win:
            bankroll *= 1 + f * (1 - price) / price
        else:
            bankroll *= 1 - f

        peak = max(peak, bankroll)
        max_dd = max(max_dd, (peak - bankroll) / peak)
        if bankroll < bankroll0 * 0.10:
            ruina = True

    return bankroll, max_dd, ruina


def _demo():
    # --- autotest de la fórmula (assert-based) ---
    assert kelly_fraction(0.5, 0.5) == 0.0  # sin edge -> no apuesta
    assert kelly_fraction(0.4, 0.5) == 0.0  # edge negativo -> no apuesta
    f = kelly_fraction(0.6, 0.5, shrink=1.0, kelly_frac=1.0, hard_cap=1.0)
    assert abs(f - 0.2) < 1e-9, f  # kelly completo conocido: (0.6-0.5)/(1-0.5) = 0.2
    capped = kelly_fraction(0.9, 0.5, shrink=1.0, kelly_frac=1.0, hard_cap=0.02)
    assert capped == 0.02  # el hard cap manda aunque kelly diga más
    print("autotest OK\n")

    # --- comparación empírica de estrategias de sizing ---
    strategies = {
        "kelly completo (sin shrink, sin cap)": lambda p, pr: kelly_fraction(
            p, pr, shrink=1.0, kelly_frac=1.0, hard_cap=1.0
        ),
        "kelly conservador (shrink .5, 1/4 kelly, cap 2%)": kelly_fraction,
        "tamaño fijo 5% (sin disciplina de edge)": lambda p, pr: 0.05
        if p > pr
        else 0.0,
    }

    print(f"{'estrategia':45s} {'bankroll final':>15s} {'max drawdown':>13s} {'ruina':>7s}")
    for name, fn in strategies.items():
        finals, dds, ruinas = [], [], 0
        for seed in range(30):
            final, dd, ruina = simulate(fn, seed=seed)
            finals.append(final)
            dds.append(dd)
            ruinas += ruina
        print(
            f"{name:45s} {statistics.median(finals):15.1f} "
            f"{statistics.median(dds) * 100:12.1f}% {ruinas:6d}/30"
        )


if __name__ == "__main__":
    _demo()

"""Prueba la formula de sizing (risk_sizing.kelly_fraction) contra datos
REALES y publicos de Polymarket: order books en vivo via CLOB API, sin
autenticacion, sin costo, sin operar nada.

No hay backtest historico ni prediccion direccional aqui: solo se mide el
spread real (bid-ask) de mercados activos y se ve que tamano de posicion
sugeriria la formula si ese spread fuera el "edge" a capturar haciendo de
market maker (comprar barato, vender caro, sin apostar a una direccion).

Comisiones: Polymarket no cobra a quien pone liquidez (maker), solo a quien
la toma (taker). La formula real, tomada de su Help Center, es:
    fee_por_accion = feeRate * precio * (1 - precio)
`feeRate` viene en el propio market (`feeSchedule.rate`), asi que se usa el
dato real de cada mercado, no un numero inventado.
"""

from risk_sizing import kelly_fraction
from market_data import pick_active_markets, book_spread, taker_fee_per_share


def main(bankroll=1000.0):
    markets = pick_active_markets()
    header = (
        f"{'mercado':40s} {'spread':>7s} {'fee/accion':>10s} "
        f"{'neto maker-maker':>17s} {'neto peor caso':>15s} {'tamano ($)':>11s}"
    )
    print(header)
    net_worst_list = []
    for slug, token_id, _vol, fee_rate in markets:
        try:
            result = book_spread(token_id)
        except Exception as e:
            print(f"{slug:40s}  error: {e}")
            continue
        if result is None:
            continue
        best_bid, best_ask, bid_size, ask_size = result
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2

        # caso ideal: ambas patas son maker (poner orden, no cruzar el
        # spread) -> Polymarket cobra 0 al maker, el edge neto es el spread
        net_maker_maker = spread

        # peor caso conservador: al menos una pata se ejecuta como taker
        # (ej. salida forzada por un circuit breaker) -> se paga la comision
        # real de ese mercado sobre esa pata
        fee = taker_fee_per_share(mid, fee_rate)
        net_worst = spread - fee
        net_worst_list.append(net_worst)

        # el sizing usa siempre el escenario mas conservador (neto peor caso)
        f = kelly_fraction(p_hat=best_bid + max(net_worst, 0), price=best_bid)
        dollars = f * bankroll
        print(
            f"{slug:40s} {spread:7.3f} {fee:10.4f} "
            f"{net_maker_maker:17.3f} {net_worst:15.4f} {dollars:11.2f}"
        )

    negativos = sum(1 for x in net_worst_list if x <= 0)
    print(f"\nmercados donde la comision se come TODO el margen: {negativos}/{len(net_worst_list)}")


if __name__ == "__main__":
    main()

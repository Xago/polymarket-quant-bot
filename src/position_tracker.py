"""Cierra el loop entre 'decidimos entrar' y 'esto realmente gano o perdio'.

Una posicion simulada = comprar en el bid, vender en el ask (market making).
Se considera "cerrada con ganancia" si el precio real, despues de abrir la
posicion, efectivamente toca el precio de salida (visto en el historial
publico de precios). Si no lo toca dentro de `timeout_seconds`, se cierra
por timeout con pnl 0 -- no sabemos si hubiera funcionado, asi que no se
cuenta como ganancia.

ponytail: no distingue si el cierre fue como maker o como taker (para eso
haria falta el historial de trades con lado tomador/hacedor, no solo la
serie de precio). Se asume siempre el escenario ya conservador (edge neto
post-comision) calculado al abrir la posicion. Subir a precision de trade
real, si esto se usa para decidir plata de verdad en Etapa 3.
"""

import json
import os
from dataclasses import asdict, dataclass


@dataclass
class Position:
    slug: str
    token_id: str
    entry_price: float
    exit_target: float
    size_usd: float
    edge_per_share: float
    opened_ts: float
    timeout_seconds: float
    status: str = "open"  # open | closed_win | closed_timeout

    @property
    def shares(self):
        return self.size_usd / self.entry_price


def load_ledger(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        raw = json.load(f)
    return [Position(**p) for p in raw]


def save_ledger(path, positions):
    with open(path, "w") as f:
        json.dump([asdict(p) for p in positions], f, indent=2)


def open_position(slug, token_id, entry_price, exit_target, size_usd, edge_per_share, opened_ts, timeout_seconds):
    return Position(
        slug=slug, token_id=token_id, entry_price=entry_price, exit_target=exit_target,
        size_usd=size_usd, edge_per_share=edge_per_share, opened_ts=opened_ts,
        timeout_seconds=timeout_seconds,
    )


def resolve_position(position, get_history, now_ts):
    """(cerro: bool, pnl: float|None, razon: str|None).

    cerro=False -> sigue abierta, todavia no hay que decidir nada.

    El cruce de precio solo cuenta si paso DENTRO de la ventana de timeout,
    sin importar cuanto haya tardado el proceso en efectivamente chequear
    (si hubo un hueco de horas sin chequear, no se debe contar como
    "capturo el spread" un cruce que en realidad paso mucho despues de
    que la posicion real ya deberia haberse cerrado por timeout).
    """
    deadline = position.opened_ts + position.timeout_seconds
    lookback_until = min(now_ts, deadline)
    history = get_history(position.token_id, position.opened_ts, lookback_until)
    tocó_precio_salida = any(p >= position.exit_target for _t, p in history)

    if tocó_precio_salida:
        pnl = position.shares * position.edge_per_share
        return True, pnl, "spread_capturado"

    if now_ts >= deadline:
        return True, 0.0, "timeout_sin_cierre"

    return False, None, None


def _demo():
    import tempfile

    # A: el precio cruza el objetivo -> gana lo que dice el edge
    pos_a = open_position("m-a", "tok-a", entry_price=0.40, exit_target=0.42,
                           size_usd=10.0, edge_per_share=0.015, opened_ts=1000, timeout_seconds=3600)
    closed, pnl, reason = resolve_position(
        pos_a, get_history=lambda tid, s, e: [(1000, 0.40), (1500, 0.41), (2000, 0.425)], now_ts=2000,
    )
    assert closed is True and reason == "spread_capturado"
    assert abs(pnl - (10.0 / 0.40) * 0.015) < 1e-9

    # B: nunca cruza y se pasa el timeout -> cierra en 0, no como perdida inventada
    pos_b = open_position("m-b", "tok-b", entry_price=0.40, exit_target=0.42,
                           size_usd=10.0, edge_per_share=0.015, opened_ts=1000, timeout_seconds=1000)
    closed, pnl, reason = resolve_position(
        pos_b, get_history=lambda tid, s, e: [(1000, 0.40), (1500, 0.405)], now_ts=2100,
    )
    assert closed is True and pnl == 0.0 and reason == "timeout_sin_cierre"

    # C: todavia no cruza y todavia no vencio el timeout -> sigue abierta
    pos_c = open_position("m-c", "tok-c", entry_price=0.40, exit_target=0.42,
                           size_usd=10.0, edge_per_share=0.015, opened_ts=1000, timeout_seconds=3600)
    closed, pnl, reason = resolve_position(
        pos_c, get_history=lambda tid, s, e: [(1000, 0.40), (1500, 0.405)], now_ts=1600,
    )
    assert closed is False and pnl is None and reason is None

    # D: guardar y cargar el ledger no pierde datos
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "positions.json")
        save_ledger(path, [pos_a, pos_c])
        loaded = load_ledger(path)
        assert len(loaded) == 2
        assert loaded[0].slug == "m-a" and loaded[1].slug == "m-c"

    print("position_tracker autotest OK (4 escenarios)")


if __name__ == "__main__":
    _demo()

"""Kill switch manual: un archivo centinela, no un flag en memoria.

Funciona incluso si el proceso del bot esta en un estado inconsistente,
porque no depende de que el loop principal siga vivo o sano: cualquier
proceso (el bot, vos desde la terminal, un cron de monitoreo) puede
crear/borrar el archivo. Ver docs/RISK_CONTROLS.md #5.
"""

import os
import time

DEFAULT_PATH = "KILL_SWITCH"


def is_active(path=DEFAULT_PATH):
    return os.path.exists(path)


def activate(reason, path=DEFAULT_PATH):
    with open(path, "w") as f:
        f.write(f"{time.time()} {reason}\n")


def deactivate(path=DEFAULT_PATH):
    """Accion manual explicita: el bot nunca se auto-reactiva."""
    if os.path.exists(path):
        os.remove(path)


def _demo():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "KILL_SWITCH")

        assert is_active(path) is False

        activate("prueba manual", path)
        assert is_active(path) is True

        deactivate(path)
        assert is_active(path) is False

    print("kill_switch autotest OK")


if __name__ == "__main__":
    _demo()

"""Comparar dos G-code largos sin que pytest intente un diff de miles de lineas (se queda minutos calculandolo)."""
import pytest


def assert_same_gcode(a, b, what: str = "") -> None:
    if isinstance(a, bytes):
        a = a.decode("ascii")
    if isinstance(b, bytes):
        b = b.decode("ascii")
    if a == b:
        return
    la, lb = a.splitlines(), b.splitlines()
    n = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
    pytest.fail(f"{what}: el G-code difiere en la linea {n + 1}: {la[n:n + 1]} != {lb[n:n + 1]} "
                f"({len(la)} y {len(lb)} lineas)", pytrace=False)

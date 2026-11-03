"""Verificador independiente de G-code GRBL.

El codigo vive ahora en cnc/verify_nc.py (la aplicacion lo usa antes de escribir cada programa); este archivo se
queda para usarlo suelto:  python verificar_nc.py salida.nc [--travel X Y Z] [--max-feed 3600]
"""
import sys

from cnc.verify_nc import *  # noqa: F401,F403  (analyze, Report, format_report, main...)
from cnc.verify_nc import main

if __name__ == "__main__":
    sys.exit(main())

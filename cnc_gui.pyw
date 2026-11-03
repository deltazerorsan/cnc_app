"""Lanzador de la ventana de cnc_app (doble clic, o `python cnc_gui.py [dibujo.svg]`)."""
import sys

from cnc.gui.app import main

if __name__ == "__main__":
    sys.exit(main())

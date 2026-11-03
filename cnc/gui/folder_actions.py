"""Las dos carpetas fijas de la ventana (trabajos y G-code): recordarlas en el PC, cambiarlas y usarlas.

Es parte de MainWindow (se mezcla con ella). Las reglas para decir si una ruta vale estan en `cnc.folders` (sin Qt, con
sus pruebas); aqui solo se guardan en el PC (con QSettings; en las pruebas, en memoria) y se pregunta cuando la carpeta
configurada ya no esta disponible: nunca se cambia por otra en silencio.
"""
from __future__ import annotations

from typing import Mapping, Optional

from PySide6.QtWidgets import QDialog, QMessageBox

from .. import folders as fo
from ..pipeline import Message
from .folders_dialog import FoldersDialog
from ..i18n import _

PREF_KEYS = {fo.JOBS: "folder_trabajos", fo.GCODE: "folder_gcode"}


class FolderActions:
    """Mezcla para MainWindow: necesita `settings` (o None), `_memory_prefs` (un dict), `_folder()`, `messages` y
    `statusBar()`."""

    # --- lo que se recuerda en este PC ----------------------------------------------------------------------------
    def _pref(self, key: str) -> str:
        if self.settings is not None:
            return str(self.settings.value(key, ""))
        return self._memory_prefs.get(key, "")

    def _set_pref(self, key: str, value: str) -> None:
        if self.settings is not None:
            self.settings.setValue(key, value)
            self.settings.sync()                        # al disco ya: que no dependa de cerrar bien el programa
        else:
            self._memory_prefs[key] = value

    def configured_folder(self, kind: str) -> str:
        """La carpeta fija de `kind` ("" = sin configurar)."""
        return fo.clean(self._pref(PREF_KEYS[kind]))

    def set_configured_folders(self, values: Mapping[str, str]) -> None:
        """Guarda las carpetas que vengan en `values`. Una ruta que no vale se rechaza entera (no se guarda ninguna)."""
        for kind, text in values.items():
            why = fo.describe(kind, text)
            if why:
                raise ValueError(why)
        for kind, text in values.items():
            self._set_pref(PREF_KEYS[kind], fo.clean(text))

    def open_folders_dialog(self) -> bool:
        """Archivo → Carpetas…; True si se ha aceptado."""
        dialog = FoldersDialog({kind: self.configured_folder(kind) for kind in fo.KINDS}, self)
        if dialog.exec() != QDialog.Accepted:
            return False
        self.set_configured_folders(dialog.values())
        text = ", ".join(f"{fo.LABELS[k]}: {v or _('sin configurar')}" for k, v in dialog.values().items())
        self.statusBar().showMessage(_("Carpetas guardadas. {text}", text=text))
        return True

    def folder_notes(self) -> list:
        """Avisos de arranque: una carpeta configurada que ya no vale (unidad desconectada, borrada...)."""
        notes = []
        for kind in fo.KINDS:
            why = fo.describe(kind, self.configured_folder(kind))
            if why:
                notes.append(Message(_("{why} Corrígela en Archivo → Carpetas de trabajos y G-code…; mientras tanto, la aplicación "
                                       "preguntará antes de usar otra.", why=why), "warning"))
        return notes

    # --- usarlas --------------------------------------------------------------------------------------------------
    def folder_for(self, kind: str) -> Optional[str]:
        """Carpeta por la que empieza la ventana de abrir o guardar de `kind`; None si se cancela.

        Sin configurar, la carpeta de siempre (la del dibujo). Configurada y buena, ella. Configurada y mala, se
        pregunta: configurarla de nuevo, usar la del dibujo solo esta vez o cancelar."""
        configured = self.configured_folder(kind)
        start, why = fo.start_folder(configured, self._folder())
        if why is None:
            return start
        choice = self._folder_problem_choice(fo.describe(kind, configured))
        if choice == "configure":
            if not self.open_folders_dialog():
                return None
            start, why = fo.start_folder(self.configured_folder(kind), self._folder())
            return start if why is None else None
        return start if choice == "last" else None

    def _folder_problem_choice(self, text: str) -> str:
        """Pregunta que hacer con una carpeta configurada que no vale: "configure", "last" o "cancel". Sin ventana de
        verdad (pruebas) se cancela, que es lo que no estropea nada."""
        if self.settings is None:
            return "cancel"
        box = QMessageBox(QMessageBox.Warning, _("Carpeta no disponible"), _("{text}\n\n¿Qué quieres hacer?", text=text), parent=self)
        configure = box.addButton(_("Configurar…"), QMessageBox.AcceptRole)
        last = box.addButton(_("Usar la carpeta del dibujo esta vez"), QMessageBox.ActionRole)
        cancel = box.addButton(_("Cancelar"), QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        return "configure" if clicked is configure else "last" if clicked is last else "cancel"

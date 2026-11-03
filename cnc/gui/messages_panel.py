"""Mensajes del ultimo calculo: giro, tablero, avisos y errores."""
from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from ..pipeline import Message
from . import theme
from ..i18n import _

ICONS = {"info": "•", "warning": "⚠", "error": "✖"}


class MessagesPanel(QWidget):
    applyOffsetRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setWordWrap(True)
        self.list.setFont(theme.mono_font())
        self.apply = QPushButton(_("Aplicar desplazamiento sugerido"))
        self.apply.setVisible(False)
        self.apply.clicked.connect(self.applyOffsetRequested)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        top = QHBoxLayout()
        top.addWidget(self.hint, 1)
        top.addWidget(self.apply)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        self.warnings = 0
        self.errors = 0

    def show_messages(self, messages: Sequence[Message], offset: Optional[tuple] = None) -> None:
        self.list.clear()
        self.warnings = self.errors = 0
        for m in messages:
            severe = m.level == "error" or m.severe
            if m.level == "error":
                self.errors += 1
            elif m.level == "warning" or m.severe:
                self.warnings += 1
            item = QListWidgetItem(f"{ICONS['error' if severe else m.level]}  {m.text}")
            if severe:
                color = theme.ERROR
            elif m.level == "warning":
                color = theme.WARN
            else:
                color = theme.TEXT
            item.setForeground(QBrush(QColor(color)))
            if severe:
                f = QFont(item.font())
                f.setBold(True)
                item.setFont(f)
            item.setFlags(Qt.ItemIsEnabled)
            self.list.addItem(item)
        self.apply.setVisible(offset is not None)
        if offset is not None:
            self.apply.setText(_("Aplicar desplazamiento sugerido (X {offset:g}, Y {offset2:g} mm)", offset=offset[0], offset2=offset[1]))
            self.hint.setText(_("Mover el dibujo hacia dentro del tablero:"))
        else:
            self.hint.setText("")

    def show_text(self, text: str, level: str = "info") -> None:
        self.show_messages([Message(text, level)])

    def texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

"""Desktop UI (PySide6). Russian interface, RTL/LTR panes, background engine, no network."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget, QCheckBox)

from . import __version__
from .app import check_memory_or_raise, format_report, make_engine, model_label, self_test
from .backend import BackendError, Cancelled
from .config import data_root, database_file, load_manifest
from .documents import read_text, write_text
from .glossary import DOMAIN_RU, DOMAINS, LocalStore
from .linguistics import ARABIC_RE, inferred_language
from .service import Translator

AR_FONT = QFont("Segoe UI", 16)
RU_FONT = QFont("Segoe UI", 12)
MODES = [("Точный", "accurate"), ("Литературный", "literary"), ("Профессиональный", "professional")]
SEV_ICON = {"critical": "⛔", "major": "⚠", "minor": "ℹ"}

STYLE = """
QWidget { background:#f6f4ef; color:#1d232b; font-size:13px; }
QMainWindow { background:#f6f4ef; }
QLabel#title { font-size:20px; font-weight:700; color:#173b3f; }
QLabel#subtitle { color:#5b6670; }
QPlainTextEdit, QTextBrowser, QListWidget, QTableWidget { background:#ffffff; border:1px solid #cfd5da;
    border-radius:8px; padding:8px; selection-background-color:#b9dcd8; }
QLineEdit, QComboBox { background:#ffffff; border:1px solid #cfd5da; border-radius:6px; padding:5px 8px; }
QPushButton { background:#ffffff; border:1px solid #b9c1c8; border-radius:6px; padding:6px 14px; }
QPushButton:hover { background:#eef3f3; }
QPushButton#primary { background:#1f6f6b; color:#ffffff; border:1px solid #1f6f6b; font-weight:600; }
QPushButton#primary:hover { background:#185b58; }
QPushButton:disabled { color:#9aa3ab; background:#eceae4; }
QProgressBar { border:1px solid #cfd5da; border-radius:4px; height:8px; text-align:center; background:#ffffff; }
QProgressBar::chunk { background:#1f6f6b; }
QLabel#status { color:#3d4852; }
"""


def set_direction(widget, arabic: bool) -> None:
    widget.setLayoutDirection(Qt.RightToLeft if arabic else Qt.LeftToRight)
    doc = widget.document()
    opt = doc.defaultTextOption()
    opt.setTextDirection(Qt.RightToLeft if arabic else Qt.LeftToRight)
    opt.setAlignment(Qt.AlignRight if arabic else Qt.AlignLeft)
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    doc.setDefaultTextOption(opt)
    widget.setFont(AR_FONT if arabic else RU_FONT)


# ------------------------------------------------------------------ background work
class Job(QObject):
    """Runs a callable in a QThread; emits result or a user-facing error string."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except Cancelled as ex:
            self.failed.emit(str(ex))
        except (BackendError, ValueError, OSError) as ex:
            self.failed.emit(str(ex))
        except Exception as ex:  # noqa: BLE001
            self.failed.emit(f"Непредвиденная ошибка: {ex!r}")


class Runner(QObject):
    """Lives in the GUI thread; worker signals arrive here as queued calls, so callbacks
    (which touch widgets) always run in the GUI thread."""

    def __init__(self):
        super().__init__()
        self.thread: QThread | None = None
        self.job: Job | None = None
        self._cb = (None, None, None)

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.isRunning()

    def start(self, job: Job, on_done, on_fail, on_progress=None):
        self.job = job
        self._cb = (on_done, on_fail, on_progress)
        self.thread = QThread()
        job.moveToThread(self.thread)
        self.thread.started.connect(job.run)
        job.done.connect(self._done, Qt.QueuedConnection)
        job.failed.connect(self._fail, Qt.QueuedConnection)
        job.progress.connect(self._progress, Qt.QueuedConnection)
        job.done.connect(self.thread.quit)
        job.failed.connect(self.thread.quit)
        self.thread.start()

    def _finish(self):
        if self.thread:
            self.thread.wait(2000)

    @Slot(object)
    def _done(self, value):
        self._finish()
        if self._cb[0]:
            self._cb[0](value)

    @Slot(str)
    def _fail(self, msg):
        self._finish()
        if self._cb[1]:
            self._cb[1](msg)

    @Slot(object)
    def _progress(self, value):
        if self._cb[2]:
            self._cb[2](value)

    def cancel(self):
        if self.job:
            self.job.cancel.set()


# ------------------------------------------------------------------ dialogs
class TermDialog(QDialog):
    def __init__(self, store: LocalStore, direction: str, preset: str = "", parent=None):
        super().__init__(parent)
        self.store, self.direction = store, direction
        self.setWindowTitle("Термин в личный словарь")
        f = QFormLayout(self)
        self.source = QLineEdit(preset)
        self.target = QLineEdit()
        self.forbidden = QLineEdit()
        self.forbidden.setPlaceholderText("через ; — варианты, которые нельзя использовать")
        self.domain = QComboBox()
        for d in DOMAINS:
            self.domain.addItem(DOMAIN_RU[d], d)
        self.note = QLineEdit()
        src_ar = direction == "ar-ru"
        self.source.setLayoutDirection(Qt.RightToLeft if src_ar else Qt.LeftToRight)
        self.target.setLayoutDirection(Qt.LeftToRight if src_ar else Qt.RightToLeft)
        f.addRow("Термин (" + ("арабский" if src_ar else "русский") + ")", self.source)
        f.addRow("Перевод (" + ("русский" if src_ar else "арабский") + ")", self.target)
        f.addRow("Запрещённые варианты", self.forbidden)
        f.addRow("Область", self.domain)
        f.addRow("Примечание", self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def save(self):
        try:
            tgt = self.target.text().strip()
            self.store.add_term({"lang": self.direction[:2], "source": self.source.text(), "preferred": tgt,
                                 "forbidden": [x.strip() for x in self.forbidden.text().split(";") if x.strip()],
                                 "domain": self.domain.currentData(), "notes": {tgt: self.note.text()},
                                 "origin": "user", "status": "approved", "priority": 80,
                                 "sources": [{"code": "USER", "kind": "user", "title": "Добавлено пользователем"}]})
            self.accept()
        except ValueError as ex:
            QMessageBox.warning(self, "Словарь", str(ex))


class GlossaryDialog(QDialog):
    def __init__(self, store: LocalStore, direction: str, parent=None):
        super().__init__(parent)
        self.store, self.direction = store, direction
        self.setWindowTitle("Личный словарь терминов (хранится только на этом компьютере)")
        self.resize(900, 520)
        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Термин", "Перевод", "Запрещено", "Область", "Статус", "id"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setColumnHidden(5, True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.table)
        self.conflicts = QLabel()
        self.conflicts.setWordWrap(True)
        v.addWidget(self.conflicts)
        h = QHBoxLayout()
        for text, fn in [("Добавить", self.add), ("Удалить", self.delete), ("Импорт…", self.imp),
                         ("Экспорт…", self.exp)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        h.addWidget(close)
        v.addLayout(h)
        self.reload()

    def reload(self):
        rows = [t for t in self.store.terms(self.direction) if not t.get("reverse")]
        self.table.setRowCount(len(rows))
        for i, t in enumerate(rows):
            for j, val in enumerate([t["source"], t["target"], "; ".join(t["forbidden"]),
                                     DOMAIN_RU.get(t["domain"], t["domain"]),
                                     {"draft": "черновик", "approved": "утверждён", "reviewed": "проверен"}.get(
                                         t["status"], t["status"]), str(t["id"])]):
                self.table.setItem(i, j, QTableWidgetItem(val))
        conf = self.store.conflicts(self.direction)
        self.conflicts.setText("Конфликты терминов: " + "; ".join(f"«{s}» → {', '.join(v)}" for s, v in conf)
                               if conf else "Конфликтов терминов нет.")

    def add(self):
        if TermDialog(self.store, self.direction, parent=self).exec():
            self.reload()

    def delete(self):
        r = self.table.currentRow()
        if r >= 0 and QMessageBox.question(self, "Словарь", "Удалить выбранный термин?") == QMessageBox.Yes:
            self.store.delete_term(int(self.table.item(r, 5).text()))
            self.reload()

    def imp(self):
        p, _ = QFileDialog.getOpenFileName(self, "Импорт словаря", "", "Словарь Arabica (*.json)")
        if p:
            try:
                n = self.store.import_json(p)
                QMessageBox.information(self, "Импорт", f"Импортировано терминов: {n}")
                self.reload()
            except (ValueError, OSError, KeyError) as ex:
                QMessageBox.warning(self, "Импорт", str(ex))

    def exp(self):
        p, _ = QFileDialog.getSaveFileName(self, "Экспорт словаря", "arabica-glossary.json", "JSON (*.json)")
        if p:
            n = self.store.export_json(p)
            QMessageBox.information(self, "Экспорт", f"Сохранено терминов: {n}")


class HistoryDialog(QDialog):
    def __init__(self, store: LocalStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.chosen = None
        self.setWindowTitle("История переводов — только на этом компьютере")
        self.resize(820, 480)
        v = QVBoxLayout(self)
        self.fav = QCheckBox("Только избранное")
        self.fav.toggled.connect(self.reload)
        v.addWidget(self.fav)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.open_item)
        v.addWidget(self.list)
        h = QHBoxLayout()
        for text, fn in [("Открыть", lambda: self.open_item(self.list.currentItem())),
                         ("★ В избранное", self.star), ("Очистить историю", self.clear)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        self.reload()

    def reload(self):
        self.list.clear()
        for r in self.store.recent(200, self.fav.isChecked()):
            it = QListWidgetItem(("★ " if r["favourite"] else "") + f"{r['ts'][:16].replace('T', ' ')} · "
                                 f"{'RU→AR' if r['direction'] == 'ru-ar' else 'AR→RU'} · "
                                 + r["source"].replace("\n", " ")[:90])
            it.setData(Qt.UserRole, r)
            self.list.addItem(it)

    def open_item(self, it):
        if it:
            self.chosen = it.data(Qt.UserRole)
            self.accept()

    def star(self):
        it = self.list.currentItem()
        if it:
            r = it.data(Qt.UserRole)
            self.store.set_favourite(r["id"], not r["favourite"])
            self.reload()

    def clear(self):
        if QMessageBox.question(self, "История", "Удалить всю историю, кроме избранного?") == QMessageBox.Yes:
            self.store.clear_history(keep_favourites=True)
            self.reload()


class TextDialog(QDialog):
    def __init__(self, title: str, html: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 520)
        v = QVBoxLayout(self)
        b = QTextBrowser()
        b.setOpenExternalLinks(False)
        b.setHtml(html)
        v.addWidget(b)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ar_span(s: str) -> str:
    return f'<span dir="rtl" style="font-size:18px">{esc(s)}</span>' if ARABIC_RE.search(s or "") else esc(s)


def card_html(card: dict) -> str:
    h = [f"<h2>{ar_span(card.get('headword', ''))}</h2>"]
    if card.get("vocalized"):
        h.append(f"<p>Огласовка: {ar_span(card['vocalized'])}")
        if card.get("transcription"):
            h.append(f" · транскрипция: <i>{esc(card['transcription'])}</i>")
        h.append("</p>")
    meta = []
    if card.get("lemma"):
        meta.append("словарная форма: " + ar_span(card["lemma"]))
    if card.get("part_of_speech"):
        meta.append(esc(card["part_of_speech"]) + ("" if card.get("pos_confident") else " (не уверено)"))
    if card.get("morphology"):
        meta.append(esc(card["morphology"]))
    if meta:
        h.append("<p>" + " · ".join(meta) + "</p>")
    h.append("<ol>")
    for s in card.get("senses", []):
        h.append(f"<li><b>{ar_span(s.get('translation', ''))}</b> <small>[{esc(s.get('context_label', ''))}]</small>"
                 f"<br>{ar_span(s.get('example_source', ''))}<br><i>{ar_span(s.get('example_translation', ''))}</i></li>")
    h.append("</ol>")
    if card.get("ambiguity_note"):
        h.append(f"<p>⚠ {esc(card['ambiguity_note'])}</p>")
    h.append("<p><small>Карточка составлена локальной моделью и не сверена со словарём — "
             "проверяйте важные значения.</small></p>")
    return "".join(h)


# ------------------------------------------------------------------ main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Arabica AI Pro {__version__} — русский ↔ литературный арабский")
        self.resize(1240, 800)
        self.setStyleSheet(STYLE)
        self.store = LocalStore(database_file())
        self.engine = make_engine()
        self.translator = Translator(self.engine, self.store, model_label())
        self.runner = Runner()
        self.direction = "ru-ar"
        self.result = None
        self.ready = False
        self._build()
        self._apply_direction()
        self._load_engine()

    # ---------------- layout
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 12, 18, 10)
        head = QHBoxLayout()
        t = QLabel("Arabica AI Pro")
        t.setObjectName("title")
        sub = QLabel("Офлайн-перевод: русский ↔ современный литературный арабский (фусха). Без интернета.")
        sub.setObjectName("subtitle")
        hv = QVBoxLayout()
        hv.addWidget(t)
        hv.addWidget(sub)
        head.addLayout(hv)
        head.addStretch()
        outer.addLayout(head)

        bar = QHBoxLayout()
        self.dir_btn = QPushButton()
        self.dir_btn.setToolTip("Сменить направление (Ctrl+Shift+S)")
        self.dir_btn.clicked.connect(self.swap)
        self.mode = QComboBox()
        for label, key in MODES:
            self.mode.addItem(label, key)
        self.domain = QComboBox()
        self.domain.addItem("любая область", None)
        for d in DOMAINS:
            self.domain.addItem(DOMAIN_RU[d], d)
        bar.addWidget(self.dir_btn)
        bar.addWidget(QLabel("Режим:"))
        bar.addWidget(self.mode)
        bar.addWidget(QLabel("Область:"))
        bar.addWidget(self.domain)
        bar.addStretch()
        for text, fn in [("Словарная карточка", self.word_card), ("Огласовка и транскрипция", self.vocalize),
                         ("Словарь", self.glossary), ("История", self.history)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        outer.addLayout(bar)

        self.context = QLineEdit()
        self.context.setPlaceholderText("Контекст (необязательно): о чём текст, кто говорит — помогает выбрать "
                                        "значение многозначных слов; в перевод не попадает")
        outer.addWidget(self.context)

        split = QSplitter(Qt.Horizontal)
        self.src = QPlainTextEdit()
        self.dst = QPlainTextEdit()
        self.dst.setReadOnly(True)
        self.src.setPlaceholderText("Введите или вставьте текст…  (Ctrl+Enter — перевести)")
        split.addWidget(self.src)
        split.addWidget(self.dst)
        split.setSizes([600, 600])
        outer.addWidget(split, 1)

        ctl = QHBoxLayout()
        self.go = QPushButton("Перевести")
        self.go.setObjectName("primary")
        self.go.setShortcut(QKeySequence("Ctrl+Return"))
        self.go.clicked.connect(self.translate)
        self.stop = QPushButton("Отменить")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.runner.cancel)
        copy = QPushButton("Копировать перевод")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.dst.toPlainText()))
        opn = QPushButton("Открыть TXT/DOCX…")
        opn.clicked.connect(self.open_doc)
        sav = QPushButton("Сохранить перевод…")
        sav.clicked.connect(self.save_doc)
        self.redo = QPushButton("Перевести фрагмент заново")
        self.redo.setEnabled(False)
        self.redo.clicked.connect(self.retranslate)
        for b in (self.go, self.stop, copy, opn, sav, self.redo):
            ctl.addWidget(b)
        ctl.addStretch()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setTextVisible(False)
        self.progress.hide()
        ctl.addWidget(self.progress)
        outer.addLayout(ctl)

        self.issues = QListWidget()
        self.issues.setMaximumHeight(110)
        self.issues.itemSelectionChanged.connect(lambda: self.redo.setEnabled(self._selected_segment() is not None))
        outer.addWidget(self.issues)
        self.status = QLabel("Запуск…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        m = self.menuBar().addMenu("Справка")
        a = QAction("Самопроверка (для отчёта)", self)
        a.triggered.connect(self.run_selftest)
        m.addAction(a)
        a2 = QAction("О программе и лицензии", self)
        a2.triggered.connect(self.about)
        m.addAction(a2)
        sw = QAction(self)
        sw.setShortcut(QKeySequence("Ctrl+Shift+S"))
        sw.triggered.connect(self.swap)
        self.addAction(sw)

    def _apply_direction(self):
        ar_src = self.direction == "ar-ru"
        set_direction(self.src, ar_src)
        set_direction(self.dst, not ar_src)
        self.dir_btn.setText("العربية  →  Русский   ⇄" if ar_src else "Русский  →  العربية   ⇄")

    def _busy(self, on: bool, text: str = ""):
        self.go.setEnabled(not on and self.ready)
        self.stop.setEnabled(on)
        self.progress.setVisible(on)
        if on:
            self.progress.setRange(0, 0)
        if text:
            self.status.setText(text)

    # ---------------- engine
    def _load_engine(self):
        def work(job: Job):
            from .integrity import verify_model
            job.progress.emit("Проверка целостности модели (только при первом запуске)…")
            verify_model(progress=lambda f: job.progress.emit(f"Проверка целостности модели: {int(f * 100)} %"),
                         cancel=job.cancel)
            check_memory_or_raise()
            self.engine.start(job.cancel, status=lambda s: job.progress.emit(s))
            job.progress.emit("Подготовка (первичная обработка инструкций)…")
            try:  # warm the prompt cache for the default direction/mode
                self.translator.translate("Здравствуйте.", "ru-ar", "accurate", cancel=job.cancel)
            except BackendError:
                pass
            return self.engine.load_seconds

        def ok(sec):
            self.ready = True
            self._busy(False, f"Готово · модель: {model_label()} · загрузка {sec} с · работает без интернета")

        def fail(msg):
            self._busy(False, "Модель не загружена: " + msg)
            QMessageBox.critical(self, "Arabica AI Pro", msg)

        self._busy(True, "Загрузка…")
        self.runner.start(Job(work), ok, fail, lambda s: self.status.setText(str(s)))

    # ---------------- actions
    def swap(self):
        if self.runner.busy:
            return
        self.direction = "ar-ru" if self.direction == "ru-ar" else "ru-ar"
        out = self.dst.toPlainText()
        if out:
            self.src.setPlainText(out)
            self.dst.clear()
        self.result = None
        self.issues.clear()
        self._apply_direction()

    def translate(self):
        if self.runner.busy or not self.ready:
            return
        text = self.src.toPlainText()
        if not text.strip():
            self.status.setText("Введите текст для перевода.")
            return
        self.dst.clear()
        self.issues.clear()
        d, mode, dom, note = self.direction, self.mode.currentData(), self.domain.currentData(), self.context.text()

        def work(job: Job):
            return self.translator.translate(
                text, d, mode, cancel=job.cancel, user_note=note, domain=dom,
                progress=lambda i, n: job.progress.emit((i, n)))

        def prog(p):
            if isinstance(p, tuple):
                i, n = p
                self.progress.setRange(0, n)
                self.progress.setValue(i)
                self.status.setText(f"Перевод… фрагмент {i} из {n}")

        self._busy(True, "Перевод…")
        self.runner.start(Job(work), self._translated, self._failed, prog)

    def _translated(self, r):
        self.result = r
        self.dst.setPlainText(r.text)
        self.issues.clear()
        for w in r.warnings:
            it = QListWidgetItem(f"{SEV_ICON.get(w.severity, '•')} {w.message}"
                                 + ("  (эвристика)" if w.kind == "heuristic" else ""))
            it.setData(Qt.UserRole, w.message)
            self.issues.addItem(it)
        used = ", ".join(t["source"] for t in r.glossary_used[:6])
        self._busy(False, f"Готово · фрагментов: {r.chunks}" + (f" · словарь: {used}" if used else "")
                   + (" · замечаний нет" if not r.warnings else f" · замечаний: {len(r.warnings)}"))

    def _failed(self, msg):
        self._busy(False, "Не выполнено: " + msg)

    def _selected_segment(self):
        it = self.issues.currentItem()
        if not it or not self.result:
            return None
        msg = it.data(Qt.UserRole) or ""
        if msg.startswith("Фрагмент "):
            try:
                return int(msg.split()[1].rstrip(":")) - 1
            except ValueError:
                return None
        return 0 if len(self.result.segments) == 1 else None

    def retranslate(self):
        idx = self._selected_segment()
        if idx is None or self.runner.busy:
            return
        r, d, mode = self.result, self.direction, self.mode.currentData()

        def work(job: Job):
            return self.translator.retranslate_segment(r, idx, d, mode, cancel=job.cancel,
                                                       user_note=self.context.text())

        def ok(res):
            self.result = res
            self.dst.setPlainText(res.text)
            self._busy(False, f"Фрагмент {idx + 1} переведён заново.")

        self._busy(True, f"Повторный перевод фрагмента {idx + 1}…")
        self.runner.start(Job(work), ok, self._failed)

    def _selected_word(self) -> tuple[str, str]:
        for w, d in ((self.src, self.direction), (self.dst, "ar-ru" if self.direction == "ru-ar" else "ru-ar")):
            s = w.textCursor().selectedText().strip()
            if s:
                return s, d
        s = self.src.toPlainText().strip()
        return s, self.direction

    def word_card(self):
        if self.runner.busy or not self.ready:
            return
        word, d = self._selected_word()
        if not word or len(word.split()) > 4:
            QMessageBox.information(self, "Словарная карточка",
                                    "Выделите слово (или введите одно слово) и нажмите «Словарная карточка».")
            return
        ctx = self.src.toPlainText()[:800]

        def ok(card):
            self._busy(False, "Карточка готова.")
            TextDialog("Словарная карточка", card_html(card), self).exec()

        self._busy(True, "Составление словарной карточки…")
        self.runner.start(Job(lambda job: self.translator.word_card(word, d, ctx, job.cancel)), ok, self._failed)

    def vocalize(self):
        if self.runner.busy or not self.ready:
            return
        sel = self.src.textCursor().selectedText() or self.dst.textCursor().selectedText()
        text = sel or (self.src.toPlainText() if self.direction == "ar-ru" else self.dst.toPlainText())
        text = text.strip()
        if not ARABIC_RE.search(text):
            QMessageBox.information(self, "Огласовка", "Нет арабского текста: выделите арабский фрагмент.")
            return
        if len(text) > 1500:
            QMessageBox.information(self, "Огласовка", "Выделите фрагмент до 1500 знаков.")
            return

        def ok(res):
            self._busy(False, "Огласовка готова.")
            html = ""
            if res["vocalized"]:
                html += f"<p dir='rtl' style='font-size:22px'>{esc(res['vocalized'])}</p>"
                html += f"<p><b>Транскрипция:</b> {esc(res['transcription'])}</p>"
            html += "".join(f"<p>⚠ {esc(n)}</p>" for n in res["notes"])
            html += "<p><small>Огласовка выполнена локальной моделью; буквы текста проверены на неизменность. " \
                    "Транскрипция — по правилам (см. руководство), без проверки экспертом.</small></p>"
            TextDialog("Огласовка и транскрипция", html, self).exec()

        self._busy(True, "Огласовка…")
        self.runner.start(Job(lambda job: self.translator.diacritize(text, job.cancel)), ok, self._failed)

    def glossary(self):
        sel = self.src.textCursor().selectedText().strip()
        if sel and len(sel.split()) <= 6:
            TermDialog(self.store, self.direction, sel, self).exec()
        else:
            GlossaryDialog(self.store, self.direction, self).exec()

    def history(self):
        dlg = HistoryDialog(self.store, self)
        if dlg.exec() and dlg.chosen:
            r = dlg.chosen
            self.direction = r["direction"]
            self._apply_direction()
            self.src.setPlainText(r["source"])
            self.dst.setPlainText(r["target"])

    def open_doc(self):
        p, _ = QFileDialog.getOpenFileName(self, "Открыть документ", "", "Текст и Word (*.txt *.docx)")
        if p:
            try:
                self.src.setPlainText(read_text(p))
                lang = inferred_language(self.src.toPlainText())
                hint = ""
                if lang in ("ru", "ar") and lang != self.direction[:2]:
                    hint = " · язык файла не совпадает с направлением — нажмите кнопку направления"
                self.status.setText(f"Открыт файл: {Path(p).name}{hint}")
            except (ValueError, OSError, ImportError) as ex:
                QMessageBox.warning(self, "Не удалось открыть", str(ex))

    def save_doc(self):
        if not self.dst.toPlainText():
            self.status.setText("Сначала выполните перевод.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Сохранить перевод", "перевод.docx",
                                           "Word (*.docx);;Текст (*.txt)")
        if p:
            try:
                write_text(p, self.dst.toPlainText(), rtl=self.direction == "ru-ar")
                self.status.setText("Сохранено: " + p)
            except (ValueError, OSError, ImportError) as ex:
                QMessageBox.warning(self, "Не удалось сохранить", str(ex))

    def run_selftest(self):
        if self.runner.busy:
            return
        self.engine.stop()
        self.ready = False

        def ok(rep):
            self._busy(False, "Самопроверка завершена: " + ("перевод работает" if rep["ok"] else "есть ошибки")
                       + f" · отчёт: {rep['report_path']}")
            TextDialog("Самопроверка — сфотографируйте этот экран",
                       f"<pre style='font-size:12px'>{esc(format_report(rep).split(chr(10) + chr(10) + '{')[0])}</pre>"
                       f"<p>Полный отчёт сохранён: {esc(rep['report_path'])}</p>", self).exec()
            self._load_engine()

        self._busy(True, "Самопроверка…")
        self.runner.start(Job(lambda job: self_test(lambda s: job.progress.emit(s), job.cancel)), ok,
                          lambda m: (self._failed(m), self._load_engine()),
                          lambda s: self.status.setText(str(s)))

    def about(self):
        m = load_manifest()
        html = (f"<h3>Arabica AI Pro {__version__}</h3><p>Офлайн-переводчик русский ↔ современный литературный "
                f"арабский. Работает полностью на этом компьютере, без интернета; данные хранятся в "
                f"{esc(str(data_root()))}.</p>"
                f"<p><b>Модель:</b> {esc(m.get('display_name', '?'))}<br>Источник: {esc(m.get('source', ''))}<br>"
                f"Лицензия модели: {esc(m.get('license', ''))}<br>SHA-256: <small>{esc(m.get('sha256', ''))}</small></p>"
                f"<p><b>Движок:</b> llama.cpp {esc(m.get('engine', ''))} (MIT)</p>"
                "<p>Перевод выполняет нейросеть: возможны ошибки. Важные тексты проверяйте. "
                "Тексты лицензий — в папке LICENSES рядом с программой.</p>")
        TextDialog("О программе", html, self).exec()

    def closeEvent(self, event):
        self.runner.cancel()
        if self.runner.thread:
            self.runner.thread.wait(8000)
        self.engine.stop()
        event.accept()


def main() -> int:
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName("Arabica AI Pro")
    w = MainWindow()
    w.show()
    return app.exec()

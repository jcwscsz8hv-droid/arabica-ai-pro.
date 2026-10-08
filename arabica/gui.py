"""Desktop UI: PySide6 imported only when the GUI actually starts."""
from __future__ import annotations
import threading
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextOption, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QComboBox, QGroupBox, QMessageBox,
    QFileDialog, QDialog, QFormLayout, QLineEdit, QListWidget,
)
from .backend import EngineConfig, LlamaCliEngine, BackendError, Cancelled, check_files
from .config import runtime_file, model_file, database_file
from .glossary import LocalStore
from .documents import read_text, write_text
from .service import Translator


class Worker(QThread):
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self, source: str, direction: str, mode: str, store):
        super().__init__()
        self.source = source
        self.direction = direction
        self.mode = mode
        self.store = store
        self.cancel_flag = threading.Event()

    def run(self):
        try:
            engine = LlamaCliEngine(EngineConfig(runtime_file(), model_file()))
            result = Translator(engine,self.store).translate(
                self.source, self.direction, self.mode,
                self.cancel_flag, lambda a,b: self.progress.emit(a,b))
            self.completed.emit(result)
        except (BackendError,Cancelled,ValueError,OSError) as ex:
            self.failed.emit(str(ex))
        except Exception as ex:
            self.failed.emit("Непредвиденная ошибка: " + str(ex))


class TermDialog(QDialog):
    def __init__(self, store: LocalStore, direction: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.direction = direction
        self.setWindowTitle("Добавить термин в локальный словарь")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source = QLineEdit()
        self.target = QLineEdit()
        self.domain = QComboBox()
        self.domain.addItems(["general","political","diplomatic","military","science","literary"])
        form.addRow("Оригинал", self.source)
        form.addRow("Перевод", self.target)
        form.addRow("Область", self.domain)
        layout.addLayout(form)
        save = QPushButton("Сохранить")
        save.clicked.connect(self.save)
        layout.addWidget(save)
    def save(self):
        try:
            self.store.put_term(self.source.text(),self.target.text(),
                self.direction,self.domain.currentText())
            self.accept()
        except ValueError as ex:
            QMessageBox.warning(self,"Ошибка",str(ex))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Arabica AI Pro — русский ↔ литературный арабский")
        self.resize(1170,760)
        self.store = LocalStore(database_file())
        self.worker = None
        self.direction = "ru-ar"
        self._make_ui()
        self._set_language_direction()
        try:
            check_files(EngineConfig(runtime_file(),model_file()))
            self.status.setText("Локальная модель найдена · готово к переводу")
        except BackendError:
            self.status.setText("Демо исходного проекта: в комплекте НЕТ модели и llama.cpp. Перевод пока недоступен.")
        self.setStyleSheet("""
          QWidget { background:#111824; color:#edf2fc; font-size:13px; }
          QMainWindow { background:#0c1320; }
          QLabel#heading { font-size:23px; font-weight:700; color:#f9fbff; }
          QLabel#subheading { color:#9baec6; }
          QLabel#status { color:#a6b9d0; font-size:12px; }
          QGroupBox { background:#172234; border:1px solid #2c3b50; border-radius:12px;
              margin-top:12px; padding:15px 10px 10px; font-weight:600; }
          QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left;
              padding:0 9px; color:#e0ebfc; }
          QTextEdit { background:#0d1624; border:1px solid #334459; border-radius:10px;
              selection-background-color:#315a90; padding:11px; line-height:1.4; }
          QLineEdit,QComboBox { background:#1d2a40; border:1px solid #43516a;
              border-radius:7px; padding:8px; }
          QPushButton { background:#253752; border:1px solid #425a78;
              border-radius:8px; padding:9px 16px; font-weight:600; }
          QPushButton:hover { background:#315175; }
          QPushButton#translate { background:#2e79b6; border:1px solid #55a0d3; }
          QPushButton:disabled { color:#6d8196; background:#233042; }
        """)

    def _make_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24,20,24,20)
        outer.setSpacing(13)
        head = QLabel("ARABICA  AI  PRO")
        head.setObjectName("heading")
        sub = QLabel("Профессиональный локальный перевод · русский ↔ литературный арабский (фусха)")
        sub.setObjectName("subheading")
        outer.addWidget(head)
        outer.addWidget(sub)
        row = QHBoxLayout()
        self.dir_btn = QPushButton("Русский → العربية  ⇄")
        self.dir_btn.clicked.connect(self.swap)
        self.mode_box = QComboBox()
        self.mode_box.addItem("Точный", "accurate")
        self.mode_box.addItem("Литературный", "literary")
        self.mode_box.addItem("Профессиональный", "professional")
        term_btn = QPushButton("+ Термин")
        term_btn.clicked.connect(self.add_term)
        history_btn = QPushButton("История")
        history_btn.clicked.connect(self.history)
        row.addWidget(self.dir_btn)
        row.addWidget(QLabel("Режим:"))
        row.addWidget(self.mode_box)
        row.addStretch()
        row.addWidget(term_btn)
        row.addWidget(history_btn)
        outer.addLayout(row)
        panes = QHBoxLayout()
        group_in = QGroupBox("ИСХОДНЫЙ ТЕКСТ")
        group_out = QGroupBox("ПЕРЕВОД")
        li = QVBoxLayout(group_in)
        lo = QVBoxLayout(group_out)
        self.src = QTextEdit()
        self.dst = QTextEdit()
        self.dst.setReadOnly(True)
        self.src.setPlaceholderText("Введите текст...\nДля нескольких абзацев сохраняется контекст.")
        self.dst.setPlaceholderText("Результат будет здесь")
        self.src.setFont(QFont("Segoe UI",13))
        self.dst.setFont(QFont("Segoe UI",13))
        li.addWidget(self.src)
        lo.addWidget(self.dst)
        panes.addWidget(group_in,1)
        panes.addWidget(group_out,1)
        outer.addLayout(panes,1)
        controls = QHBoxLayout()
        self.translate_btn = QPushButton("Перевести")
        self.translate_btn.setObjectName("translate")
        self.translate_btn.clicked.connect(self.translate)
        self.cancel_btn = QPushButton("Отменить")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        import_btn = QPushButton("Открыть TXT / DOCX")
        import_btn.clicked.connect(self.open_document)
        export_btn = QPushButton("Сохранить перевод")
        export_btn.clicked.connect(self.save_document)
        copy_btn = QPushButton("Скопировать")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.dst.toPlainText()))
        for b in [self.translate_btn,self.cancel_btn,import_btn,export_btn,copy_btn]:
            controls.addWidget(b)
        controls.addStretch()
        outer.addLayout(controls)
        self.status = QLabel("Запуск...")
        self.status.setObjectName("status")
        outer.addWidget(self.status)

    def _set_language_direction(self):
        src_ar = self.direction == "ar-ru"
        for widget, is_ar in [(self.src,src_ar),(self.dst,not src_ar)]:
            widget.setLayoutDirection(Qt.RightToLeft if is_ar else Qt.LeftToRight)
            opts = widget.document().defaultTextOption()
            opts.setTextDirection(Qt.RightToLeft if is_ar else Qt.LeftToRight)
            widget.document().setDefaultTextOption(opts)
            widget.setAlignment(Qt.AlignRight if is_ar else Qt.AlignLeft)
            widget.setFont(QFont("Segoe UI", 15 if is_ar else 13))
        self.dir_btn.setText("العربية → Русский  ⇄" if src_ar else "Русский → العربية  ⇄")

    def swap(self):
        if self.worker and self.worker.isRunning():
            return
        self.direction = "ar-ru" if self.direction == "ru-ar" else "ru-ar"
        old_dst = self.dst.toPlainText()
        if old_dst:
            self.src.setPlainText(old_dst)
            self.dst.clear()
        self._set_language_direction()

    def add_term(self):
        TermDialog(self.store,self.direction,self).exec()

    def history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("История переводов — только на этом компьютере")
        dialog.resize(650,380)
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        for row in self.store.recent():
            source = row['source'].replace('\n',' ')[:100]
            items.addItem(f"{row['ts'][:16]} · {row['direction']} · {source}")
        layout.addWidget(items)
        clear = QPushButton("Очистить историю")
        clear.clicked.connect(lambda: (self.store.clear_history(),items.clear()))
        layout.addWidget(clear)
        dialog.exec()

    def translate(self):
        if self.worker and self.worker.isRunning():
            return
        text = self.src.toPlainText()
        if not text.strip():
            self.status.setText("Введите текст для перевода.")
            return
        self.worker = Worker(text,self.direction,self.mode_box.currentData(),self.store)
        self.worker.progress.connect(lambda i,n: self.status.setText(f"Переведено фрагментов: {i}/{n}"))
        self.worker.completed.connect(self.finished)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self._finished_thread)
        self.translate_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status.setText("Запуск локальной нейросети на процессоре...")
        self.worker.start()

    def cancel(self):
        if self.worker:
            self.worker.cancel_flag.set()
            self.status.setText("Отмена выполняется...")

    def finished(self,result):
        self.dst.setPlainText(result.text)
        if result.warnings:
            self.status.setText("Перевод завершён · замечания: " + "; ".join(result.warnings[:3]))
        else:
            self.status.setText(f"Перевод завершён · фрагментов: {result.chunks}")

    def failed(self,reason):
        self.status.setText("Ошибка: " + reason)
        QMessageBox.warning(self,"Перевод не завершён",reason)

    def _finished_thread(self):
        self.translate_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def open_document(self):
        path,_ = QFileDialog.getOpenFileName(self,"Открыть документ", "", "Текст и Word (*.txt *.docx)")
        if path:
            try:
                self.src.setPlainText(read_text(path))
            except (ValueError,OSError,ImportError) as ex:
                QMessageBox.warning(self,"Не удалось открыть",str(ex))

    def save_document(self):
        if not self.dst.toPlainText():
            self.status.setText("Сначала выполните перевод.")
            return
        path,_ = QFileDialog.getSaveFileName(self,"Сохранить перевод", "translation.docx", "Word (*.docx);;Текст (*.txt)")
        if path:
            try:
                write_text(path,self.dst.toPlainText(),rtl=self.direction=="ru-ar")
                self.status.setText("Перевод сохранён локально: " + path)
            except (ValueError,OSError,ImportError) as ex:
                QMessageBox.warning(self,"Не удалось сохранить",str(ex))

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.cancel()
            self.worker.wait(15000)
            if self.worker.isRunning():
                event.ignore()
                self.status.setText("Завершение активного процесса... Попробуйте закрыть снова.")
                return
        event.accept()


def main() -> int:
    app = QApplication([])
    win = MainWindow()
    win.show()
    return app.exec()

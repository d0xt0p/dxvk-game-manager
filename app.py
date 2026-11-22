"""DXVK Game Manager — Russian Windows desktop UI, standard library only."""
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser

import core


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('DXVK Game Manager · AnyKey')
        self.geometry('950x820')
        self.minsize(820, 720)
        self.configure(bg='#f3f5f8')
        self.exe = tk.StringVar(value=sys.argv[1] if len(sys.argv) == 2 else '')
        self.source = tk.StringVar()
        self.gpu = tk.StringVar(value='NVIDIA RTX A2000')
        self.api = tk.StringVar(value='Выберите API вручную')
        self.hud = tk.BooleanVar(value=True)
        self.safe = tk.BooleanVar()
        self.info = tk.StringVar(value='Выберите настоящий EXE игры, а не лаунчер.')
        self.state_text = tk.StringVar(value='Локальная установка · Windows 10/11 · Без изменения драйверов')
        self.events = queue.Queue()
        self.busy = False
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#f3f5f8')
        style.configure('TLabel', background='#f3f5f8')
        style.configure('TCheckbutton', background='#f3f5f8')
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'), foreground='#142b48')
        style.configure('TLabelframe', background='#f3f5f8')
        style.configure('TLabelframe.Label', background='#f3f5f8', font=('Segoe UI', 11, 'bold'))
        style.configure('TButton', padding=(10, 7))
        style.configure('Accent.TButton', background='#176b77', foreground='white')
        root = ttk.Frame(self, padding=22)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='DXVK Game Manager', style='Title.TLabel').pack(anchor='w')
        ttk.Label(root, text='Выберите игру. Проверьте API. Установите с возможностью отката.').pack(anchor='w', pady=(3, 15))
        a = ttk.LabelFrame(root, text='1  Игра и пакет DXVK', padding=12)
        a.pack(fill='x')
        a.columnconfigure(1, weight=1)
        ttk.Label(a, text='EXE игры').grid(row=0, column=0, sticky='w', padx=(0, 10))
        ttk.Entry(a, textvariable=self.exe).grid(row=0, column=1, sticky='ew')
        self.action(a, 'Выбрать EXE', self.pick_exe).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(a, text='Пакет DXVK').grid(row=1, column=0, sticky='w', pady=9)
        ttk.Entry(a, textvariable=self.source).grid(row=1, column=1, sticky='ew')
        picks = ttk.Frame(a)
        picks.grid(row=1, column=2, padx=(8, 0))
        self.action(picks, 'Архив', self.pick_archive).pack(side='left')
        self.action(picks, 'Папка', self.pick_folder).pack(side='left', padx=(4, 0))
        ttk.Label(a, text='Официальный .tar.gz / .zip или распакованная папка с x32 и x64.', foreground='#536579').grid(row=2, column=1, sticky='w')
        self.action(a, 'Релизы DXVK ↗', lambda: webbrowser.open('https://github.com/doitsujin/dxvk/releases')).grid(row=2, column=2)
        self.action(a, 'Проверить EXE и статус', self.inspect).grid(row=3, column=0, columnspan=3, sticky='w', pady=(10, 0))
        ttk.Label(root, textvariable=self.info, wraplength=860, foreground='#176b77').pack(fill='x', pady=12)
        b = ttk.LabelFrame(root, text='2  API и видеокарта', padding=12)
        b.pack(fill='x')
        ttk.Label(b, text='Графический API').grid(row=0, column=0, sticky='w')
        ttk.Combobox(b, textvariable=self.api, values=list(core.DLLS), state='readonly', width=27).grid(row=0, column=1, sticky='w', padx=12)
        ttk.Label(b, text='Подсказка по импортам не определяет API достоверно.', foreground='#536579').grid(row=1, column=0, columnspan=3, sticky='w', pady=(5, 10))
        ttk.Label(b, text='Фильтр имени GPU').grid(row=2, column=0, sticky='w')
        ttk.Entry(b, textvariable=self.gpu, width=36).grid(row=2, column=1, sticky='w', padx=12)
        ttk.Checkbutton(b, text='HUD с GPU и FPS', variable=self.hud).grid(row=2, column=2, sticky='w')
        ttk.Label(b, text='Подстрока Vulkan-имени. Пустое поле — без фильтра. Ошибка в имени может помешать запуску.', wraplength=840, foreground='#536579').grid(row=3, column=0, columnspan=3, sticky='w', pady=(7, 0))
        ttk.Label(root, text='DX12 не поддерживается. Windows официально не поддерживается проектом DXVK.\nДля DXVK 3.x нужен совместимый Vulkan 1.4; наличие GPU не гарантирует нужные возможности драйвера.', foreground='#884b10', wraplength=865).pack(fill='x', pady=(12, 7))
        ttk.Checkbutton(root, text='Игра закрыта. Я проверил ограничения античита и понимаю риск блокировки аккаунта.', variable=self.safe).pack(anchor='w')
        actions = ttk.Frame(root)
        actions.pack(fill='x', pady=12)
        self.action(actions, 'Подготовить установку…', self.prepare, 'Accent.TButton').pack(side='left')
        self.action(actions, 'Удалить DXVK / откат…', self.rollback).pack(side='left', padx=8)
        self.action(actions, 'Проверить логи', self.show_logs).pack(side='left')
        self.action(actions, 'Другая папка логов', self.other_logs).pack(side='left', padx=(8, 0))
        log_frame = ttk.Frame(root)
        log_frame.pack(fill='both', expand=True)
        self.output = tk.Text(log_frame, height=9, wrap='word', font=('Consolas', 10), bg='#fff', fg='#243850', relief='flat', padx=12, pady=10)
        scroll = ttk.Scrollbar(log_frame, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.output.pack(fill='both', expand=True)
        ttk.Label(root, textvariable=self.state_text, foreground='#536579', wraplength=860).pack(anchor='w', pady=(8, 0))
        self.write('Резервные копии хранятся в .dxvk-game-manager рядом с EXE.\nНе удаляйте эту папку до отката. Повторная установка доступна после восстановления оригиналов.')
        self.after(100, self.pump)
        self.protocol('WM_DELETE_WINDOW', self.close)
        if self.exe.get():
            self.after(150, self.inspect)

    def action(self, parent, title, command, style='TButton'):
        return ttk.Button(parent, text=title, command=lambda: None if self.busy else command(), style=style)

    def write(self, text):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.insert('end', text)
        self.output.configure(state='disabled')

    def fail(self, e):
        self.write(str(e))
        messagebox.showerror('Операция остановлена', str(e), parent=self)

    def run(self, fn, done):
        self.busy = True
        self.state_text.set('Выполняется проверка / операция… Дождитесь завершения.')
        def worker():
            try:
                self.events.put((done, fn(), None))
            except Exception as e:
                self.events.put((done, None, e))
        threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        try:
            while True:
                done, result, error = self.events.get_nowait()
                self.busy = False
                self.state_text.set('Готово' if error is None else 'Операция остановлена. Подробности выше.')
                if error:
                    self.fail(error)
                else:
                    try:
                        done(result)
                    except Exception as e:
                        self.fail(e)
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def pick_exe(self):
        p = filedialog.askopenfilename(title='Выберите EXE игры', filetypes=[('Игра', '*.exe')])
        if p:
            self.exe.set(p)
            self.api.set('Выберите API вручную')
            self.safe.set(False)
            self.inspect()

    def pick_archive(self):
        p = filedialog.askopenfilename(title='Официальный архив DXVK', filetypes=[('DXVK', '*.tar.gz *.tgz *.tar *.zip'), ('Все файлы', '*.*')])
        if p:
            self.source.set(p)

    def pick_folder(self):
        p = filedialog.askdirectory(title='Папка пакета DXVK с x32 и x64')
        if p:
            self.source.set(p)

    def inspect(self):
        exe = self.exe.get()
        self.run(lambda: (core.inspect_exe(exe), core.status(exe)), self.inspected)

    def inspected(self, result):
        info, state = result
        hints = ', '.join(info['hints']) or 'не найдены'
        self.info.set(f'PE: {info["arch"]}. Признаки API: {hints}. Выберите API вручную; DLL движка и LoadLibrary здесь не видны.')
        self.write(state + '\n\nИмпорты EXE: ' + (', '.join(info['imports']) or 'нет данных') + '\n' + info['warning'])

    def prepare(self):
        if not self.safe.get():
            messagebox.showwarning('Перед установкой', 'Закройте игру и подтвердите проверку ограничений античита.', parent=self)
            return
        args = (self.exe.get(), self.source.get(), self.api.get(), self.gpu.get(), self.hud.get())
        self.run(lambda: core.plan(*args), self.preview)

    def preview(self, plan):
        entries = '\n'.join(f'• {n}: ' + ('заменить с резервной копией' if old else 'добавить') for n, old in plan['existing'].items())
        text = (f'Папка: {plan["folder"]}\nEXE: {plan["exe"]}\nAPI: {plan["api"]}, DLL: {plan["arch"]}\n'
                f'Фильтр GPU: {plan["gpu"] or "без фильтра"}\n\n{entries}\n\n'
                'Существующий dxvk.conf будет сохранён и заменён. Другие модификации с такими DLL могут перестать работать.\n'
                'Пакет должен быть получен из официальных релизов. Проверка PE не подтверждает подлинность DLL.\n\nУстановить этот набор?')
        self.write(text)
        if messagebox.askyesno('План установки', text, parent=self):
            self.run(lambda: core.install(plan), lambda m: self.write('Файлы установлены. Это ещё не подтверждение загрузки DXVK игрой.\n'
                     'Запустите игру обычным способом, проверьте HUD и затем логи.\n\n' + core.status(plan['folder'] + '/' + plan['exe'])))

    def rollback(self):
        exe = self.exe.get()
        if messagebox.askyesno('Восстановление оригиналов', 'Закройте игру. Восстановить файлы из резервной копии?\n'
                               'Добавленные утилитой файлы будут удалены. Изменённые файлы остановят откат.\n'
                               'Если DXVK уже был установлен вручную до наших изменений, он вернётся как исходный набор.\n'
                               'Неизвестные ручные установки DXVK приложение не удаляет.', parent=self):
            self.run(lambda: core.restore(exe), lambda m: self.write('Оригиналы восстановлены. Резервные копии сохранены.\n\n' + core.status(exe)))

    def show_logs(self, folder=None):
        exe = self.exe.get()
        self.run(lambda: core.logs(exe, folder), self.write)

    def other_logs(self):
        p = filedialog.askdirectory(title='Рабочая папка игры или DXVK_LOG_PATH')
        if p:
            self.show_logs(p)

    def close(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется', 'Дождитесь завершения. При аварийном прерывании используйте откат после следующего запуска.', parent=self)
        else:
            self.destroy()


if __name__ == '__main__':
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    App().mainloop()

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
import gpu_probe


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('DXVK Game Manager 0.2 · AnyKey')
        self.geometry('1020x900')
        self.minsize(950, 840)
        self.configure(bg='#f3f5f8')
        self.exe = tk.StringVar(value=sys.argv[1] if len(sys.argv) == 2 else '')
        self.source = tk.StringVar()
        self.gpu = tk.StringVar()
        self.gpu_choice = tk.StringVar(value=gpu_probe.AUTO)
        self.devices = []
        self.gpu_busy = False
        self.suggested_api = None
        self.gpu_info = tk.StringVar(value='Поиск Vulkan-видеокарт…')
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
        self.api_button = self.action(b, 'Нет подсказки API', self.apply_api)
        self.api_button.grid(row=0, column=2, sticky='w')
        self.api_button.configure(state='disabled')
        self.exe.trace_add('write', self.invalidate_analysis)
        self.action(b, 'Как выбрать API?', self.api_help).grid(row=0, column=3, sticky='e')
        ttk.Label(b, text='Подсказка по EXE и DLL движка — предположение. Активный API может отличаться.', foreground='#536579').grid(row=1, column=0, columnspan=4, sticky='w', pady=(5, 10))
        ttk.Label(b, text='Видеокарта').grid(row=2, column=0, sticky='w')
        self.gpu_box = ttk.Combobox(b, textvariable=self.gpu_choice, values=gpu_probe.choices([]), state='readonly', width=65)
        self.gpu_box.grid(row=2, column=1, columnspan=2, sticky='ew', padx=12)
        self.gpu_box.bind('<<ComboboxSelected>>', self.gpu_changed)
        self.action(b, 'Обновить GPU', self.refresh_gpus).grid(row=2, column=3, sticky='e')
        self.manual_gpu = ttk.Entry(b, textvariable=self.gpu, width=36, state='disabled')
        self.manual_gpu.grid(row=3, column=1, sticky='w', padx=12, pady=(6, 0))
        ttk.Label(b, text='Ручной фильтр').grid(row=3, column=0, sticky='w', pady=(6, 0))
        ttk.Checkbutton(b, text='HUD с GPU и FPS', variable=self.hud).grid(row=3, column=2, columnspan=2, sticky='w')
        ttk.Label(b, textvariable=self.gpu_info, wraplength=910, foreground='#536579').grid(row=4, column=0, columnspan=4, sticky='w', pady=(7, 0))
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
        self.after(200, self.refresh_gpus)
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
                self.events.put((done, fn(), None, False))
            except Exception as e:
                self.events.put((done, None, e, False))
        threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        try:
            while True:
                done, result, error, is_gpu = self.events.get_nowait()
                if is_gpu:
                    self.gpu_busy = False
                    self.gpus_loaded(result, error)
                    continue
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
        self.run(lambda: (exe, core.api_analysis(exe), core.status(exe)), self.inspected)

    def invalidate_analysis(self, *_):
        self.suggested_api = None
        self.api_button.configure(text='Сначала проверьте EXE', state='disabled')
        self.api.set('Выберите API вручную')
        self.safe.set(False)

    def inspected(self, result):
        exe, info, state = result
        if exe != self.exe.get():
            return
        hints = ', '.join(info['hints']) or 'не найдены'
        self.suggested_api = info['suggested']
        self.api_button.configure(text=f'Подсказка: {self.suggested_api}' if self.suggested_api else 'Нет однозначной подсказки',
                                  state='normal' if self.suggested_api else 'disabled')
        self.info.set(f'PE: {info["arch"]}. Признаки API: {hints}. Проверено файлов: {info["scanned"]}. '
                      + (f'Вероятно {self.suggested_api}; можно применить подсказку.' if self.suggested_api else 'Выберите API по настройкам игры; динамическая загрузка может быть не видна.'))
        evidence = '\n'.join(f'{e["api"]}: {e["file"]} → {e["import"]} (глубина {e["depth"]})' for e in info['evidence'])
        self.write(state + '\n\nПризнаки API (не подтверждение активного рендерера):\n' + (evidence or 'Не найдены')
                   + '\n\n' + '\n'.join(info['notes']) + '\n' + info['warning'])

    def apply_api(self):
        if self.suggested_api:
            self.api.set(self.suggested_api)

    def api_help(self):
        messagebox.showinfo('Как выбрать API игры',
            '1. Проверьте настройки графики или лаунчер: DX9 → D3D9, DX10 → D3D10, DX11 → D3D11.\n\n'
            '2. «Проверить EXE и статус» ищет импорты API в EXE и связанных DLL движка. '
            'Ближайшие к EXE импорты имеют приоритет над вспомогательными библиотеками. '
            'Если среди них один вариант, кнопка позволяет применить подсказку. Это не доказательство активного режима.\n\n'
            '3. Если подсказки нет или вариантов несколько — проверьте документацию конкретной игры и выбранный режим. '
            'Версия DirectX в Windows не определяет API игры.\n\n'
            'DX12, OpenGL и нативный Vulkan этим установщиком не обслуживаются. '
            'Не устанавливайте все DLL наугад. Окончательная проверка — HUD и свежие логи после запуска.', parent=self)

    def refresh_gpus(self):
        if self.gpu_busy:
            return
        self.gpu_busy = True
        self.gpu_info.set('Поиск GPU через установленный Vulkan-драйвер…')
        def worker():
            try:
                self.events.put((None, gpu_probe.scan(), None, True))
            except Exception as e:
                self.events.put((None, None, e, True))
        threading.Thread(target=worker, daemon=True).start()

    def gpus_loaded(self, devices, error):
        if error:
            self.gpu_info.set(f'{error} Доступны автовыбор и ручной ввод.')
            return
        old = self.gpu_choice.get()
        old_filter = None
        try:
            old_filter = gpu_probe.selected_filter(old, self.devices, self.gpu.get())
        except ValueError:
            pass
        self.devices = devices
        labels = gpu_probe.choices(devices)
        self.gpu_box.configure(values=labels)
        if old in labels:
            self.gpu_choice.set(old)
        else:
            matches = [labels[i + 1] for i, d in enumerate(devices) if d['name'] == old_filter]
            self.gpu_choice.set(matches[0] if len(matches) == 1 else gpu_probe.AUTO)
        self.gpu_changed()
        bits = 64 if sys.maxsize > 2**32 else 32
        self.gpu_info.set((f'Найдено Vulkan-устройств: {len(devices)}. ' if devices else 'Vulkan-устройства не найдены. ')
                          + f'Проверка {bits}-битного драйвера. Не все карты Windows доступны Vulkan; список не гарантирует совместимость с DXVK.')

    def gpu_changed(self, event=None):
        self.manual_gpu.configure(state='normal' if self.gpu_choice.get() == gpu_probe.MANUAL else 'disabled')

    def prepare(self):
        if not self.safe.get():
            messagebox.showwarning('Перед установкой', 'Закройте игру и подтвердите проверку ограничений античита.', parent=self)
            return
        try:
            gpu = gpu_probe.selected_filter(self.gpu_choice.get(), self.devices, self.gpu.get())
        except ValueError as e:
            self.fail(e)
            return
        args = (self.exe.get(), self.source.get(), self.api.get(), gpu, self.hud.get())
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
    if len(sys.argv) == 3 and sys.argv[1] == '--probe-gpus':
        try:
            gpu_probe.main(sys.argv[2])
        except Exception:
            sys.exit(2)
        sys.exit(0)
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    App().mainloop()

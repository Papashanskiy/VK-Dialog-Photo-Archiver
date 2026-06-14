import os
import time
import threading
import webbrowser
import subprocess
import json
# import winsound
import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import vk_api
import customtkinter as ctk
from PIL import Image

APP_NAME = "VK Dialog Photo Archiver"
APP_VERSION = "1.0"
CONFIG_FILE = "config.json"
MANIFEST_FILE = "download_manifest.json"
APP_AUTHOR = "ItsLouan"
APP_REPO_URL = "https://github.com/ItsLouan/VK-Dialog-Photo-Archiver"

DELAY = 0.34  # пауза между запросами к VK API, чтобы не ловить лимиты
DOWNLOAD_WORKERS = 5  # количество параллельных загрузок


def ensure_dir(path: str):
    """Создание папки, если её нет."""
    if not os.path.exists(path):
        os.makedirs(path)


def get_biggest_photo_url(photo_obj: dict) -> str:
    """
    Из объекта photo берём URL самой большой версии.
    В photo['sizes'] — список размеров с width, height, url.
    """
    sizes = photo_obj.get("sizes", [])
    if not sizes:
        return None
    best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
    return best.get("url")


def download_file_task(url: str, filepath: str, session: requests.Session, app) -> tuple:
    """
    Скачивает один файл с использованием переданной сессии.
    Возвращает (filepath, успех: bool, ошибка: str или None).
    Умеет уважать app.pause_flag.
    Мягкий стоп: поток сам не прерывается, но GUI перестаёт учитывать результат.
    """
    try:
        resp = session.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                # пауза во время скачивания
                while app.pause_flag and not app.stop_flag:
                    time.sleep(0.1)

                if not chunk:
                    continue
                f.write(chunk)
        return (filepath, True, None)
    except Exception as e:
        return (filepath, False, str(e))


def sanitize(name: str) -> str:
    """Убираем запрещённые символы Windows из имени папки."""
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip()


def photo_date_prefix(timestamp: int) -> str:
    """Возвращает дату фото в формате YYYY.MM.DD."""
    try:
        return time.strftime("%Y.%m.%d", time.localtime(int(timestamp)))
    except Exception:
        return "unknown_date"


def make_photo_key(photo_obj: dict) -> str:
    owner_id = photo_obj.get("owner_id", 0)
    photo_id = photo_obj.get("id", 0)
    date = photo_obj.get("date", 0)
    return f"{owner_id}_{photo_id}_{date}"


def allocate_photo_path(download_dir: str, timestamp: int, used_names: set[str]) -> tuple[str, str]:
    """Подбирает свободное имя вида YYYY.MM.DD (N).jpg."""
    prefix = photo_date_prefix(timestamp)
    index = 1
    while True:
        filename = f"{prefix} ({index}).jpg"
        if filename not in used_names and not os.path.exists(os.path.join(download_dir, filename)):
            used_names.add(filename)
            return filename, os.path.join(download_dir, filename)
        index += 1


def load_download_manifest(download_dir: str) -> dict:
    path = os.path.join(download_dir, MANIFEST_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_download_manifest(download_dir: str, manifest: dict):
    path = os.path.join(download_dir, MANIFEST_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Не удалось сохранить manifest: {e}")


def extract_token(text: str) -> str | None:
    """
    Принимает либо чистый токен, либо полную ссылку из адресной строки.
    Поддерживает формат vkhost и OAuth-ссылок с access_token в фрагменте.
    """
    t = text.strip()

    # Если похоже на "vk1.a.XXXX" — сразу возвращаем
    if "access_token=" not in t and "vk1.a." in t:
        return t

    # access_token может быть в части после '#'
    if "access_token=" in t:
        parts = t.split("#", 1)
        if len(parts) == 2:
            fragment = parts[1]
        else:
            fragment = t.split("?", 1)[-1]

        parsed = parse_qs(fragment, keep_blank_values=True)
        token_list = parsed.get("access_token")
        if token_list and token_list[0]:
            return token_list[0]

        start = t.find("access_token=")
        if start != -1:
            start += len("access_token=")
            end = t.find("&", start)
            if end == -1:
                return t[start:]
            return t[start:end]

    return None


def get_user_name(vk, user_id: int) -> str:
    """
    Для личного диалога берём имя/фамилию через users.get.
    Для беседы (peer_id > 2e9) возвращаем 'chat_<peer_id>'.
    """
    if user_id > 2000000000:
        return f"chat_{user_id}"

    info = vk.users.get(user_ids=user_id)[0]
    first = info.get("first_name", "")
    last = info.get("last_name", "")
    full = (last + " " + first).strip()
    return full or f"id{user_id}"


def format_time(seconds: float, use_cyrillic: bool = True) -> str:
    """Форматирует секунды в читаемый вид: '1м 23с' или '45с'."""
    minute_unit = "м" if use_cyrillic else "m"
    second_unit = "с" if use_cyrillic else "s"
    if seconds < 60:
        return f"{int(seconds)}{second_unit}"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}{minute_unit} {secs}{second_unit}"


def cyrillic_rendering_is_broken(root) -> bool:
    """
    Conda's Tk builds on Linux can be linked without Xft/fontconfig.
    Then Tk falls back to X11 bitmap fonts and renders Cyrillic as \\uXXXX.
    """
    try:
        default_font = tkfont.nametofont("TkDefaultFont")
        latin_width = default_font.measure("Privet")
        cyrillic_width = default_font.measure("Привет")
        return latin_width > 0 and cyrillic_width > latin_width * 2.2
    except Exception:
        return False


def load_config() -> dict:
    """Загружает конфигурацию из файла."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}


def save_config(config: dict):
    """Сохраняет конфигурацию в файл."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Не удалось сохранить конфиг: {e}")


def play_notification_sound():
    """Воспроизводит системный звук уведомления Windows."""
    try:
        if os.name == "nt":  # Windows
            print('Beep')
            # winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cyrillic_ui = not cyrillic_rendering_is_broken(self)
        if not self.cyrillic_ui:
            print(
                "Cyrillic rendering is broken in the current Tk build. "
                "Falling back to English UI. For Russian UI, use Python/Tk "
                "linked with Xft/fontconfig instead of this conda Tk build."
            )

        # Загружаем конфиг
        self.config_data = load_config()

        last_token = (
            self.config_data.get("last_token")
            or self.config_data.get("token")
            or self.config_data.get("access_token")
        )
        self._initial_token = last_token or ""
        self._initial_peer = str(
            self.config_data.get("last_peer_id")
            or self.config_data.get("peer_id")
            or ""
        )

        # Настройки внешнего вида
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("760x720")
        self.resizable(False, False)

        # Иконка окна из папки assets
        base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "assets", "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception as e:
                print(f"Не удалось установить иконку окна: {e}")

        # ---------- Заголовок ----------
        self.label_title = ctk.CTkLabel(
            self,
            text=APP_NAME,
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self.label_title.pack(pady=(15, 5))

        self.label_sub = ctk.CTkLabel(
            self,
            text=self.ui(
                "Выгружает все фото из диалога ВКонтакте в локальную папку.",
                "Downloads all photos from a VK dialog into a local folder.",
            ),
            font=ctk.CTkFont(size=13),
            wraplength=600,
        )
        self.label_sub.pack(pady=(0, 5))

        self.label_author = ctk.CTkLabel(
            self,
            text=f"{self.ui('Автор', 'Author')}: {APP_AUTHOR}",
            font=ctk.CTkFont(size=11),
        )
        self.label_author.pack(pady=(0, 10))

        # ---------- Поля ввода ----------
        self.frame_inputs = ctk.CTkFrame(self)
        self.frame_inputs.pack(fill="x", padx=20, pady=10)

        # Токен
        self.label_token = ctk.CTkLabel(
            self.frame_inputs, text="Access token:")
        self.label_token.grid(row=0, column=0, columnspan=3,
                              sticky="w", padx=5, pady=(10, 5))

        self.entry_token = ctk.CTkEntry(
            self.frame_inputs,
            width=420,
            show="*",
            placeholder_text=self.ui(
                "Вставьте токен или полную ссылку после авторизации",
                "Paste token or full OAuth URL",
            ),
        )
        self.entry_token.grid(row=1, column=0, padx=5, pady=(0, 8), sticky="w")

        if self._initial_token:
            self.entry_token.insert(0, self._initial_token)

        self.bind_entry_shortcuts(self.entry_token)

        # Кнопка "Получить токен"
        self.button_get_token = self.make_button(
            self.frame_inputs,
            text=self.ui("Получить токен", "Get token"),
            width=140,
            command=self.open_token_page,
        )
        self.button_get_token.grid(
            row=1, column=1, padx=(5, 5), pady=(0, 8), sticky="e")

        self.button_paste_token = self.make_button(
            self.frame_inputs,
            text=self.ui("Вставить", "Paste"),
            width=100,
            command=lambda: self.paste_into_entry(self.entry_token),
        )
        self.button_paste_token.grid(
            row=1, column=2, padx=(5, 5), pady=(0, 8), sticky="e")

        self.label_token_help = ctk.CTkLabel(
            self.frame_inputs,
            text=self.ui(
                "Нажмите кнопку выше, авторизуйтесь (права: messages, photos, offline), скопируйте всю ссылку из адресной строки.",
                "Open the token page, authorize with messages, photos, offline permissions, then copy the full URL.",
            ),
            font=ctk.CTkFont(size=11),
            wraplength=560,
        )
        self.label_token_help.grid(
            row=2, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 10))

        # peer_id
        self.label_peer = ctk.CTkLabel(
            self.frame_inputs, text=self.ui("peer_id диалога:", "dialog peer_id:"))
        self.label_peer.grid(row=3, column=0, sticky="w", padx=5, pady=(5, 5))

        self.entry_peer = ctk.CTkEntry(
            self.frame_inputs,
            width=220,
            placeholder_text=self.ui(
                "например, 12345678", "for example, 12345678"),
        )
        self.entry_peer.grid(row=4, column=0, sticky="w", padx=5, pady=(0, 5))
        if self._initial_peer:
            self.entry_peer.insert(0, self._initial_peer)
        self.bind_entry_shortcuts(self.entry_peer)

        self.label_peer_help = ctk.CTkLabel(
            self.frame_inputs,
            text=self.ui(
                "ЛС: id пользователя. Беседа: 2000000000 + chat_id.",
                "Direct message: user id. Group chat: 2000000000 + chat_id.",
            ),
            font=ctk.CTkFont(size=11),
        )
        self.label_peer_help.grid(
            row=5, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 10))

        # Папка скачивания
        self.download_root = self.config_data.get(
            "download_root") or os.getcwd()

        self.label_folder = ctk.CTkLabel(
            self.frame_inputs,
            text=self.ui("Папка для скачивания:", "Download folder:"),
        )
        self.label_folder.grid(row=6, column=0, columnspan=3,
                               sticky="w", padx=5, pady=(5, 5))

        self.entry_folder = ctk.CTkEntry(
            self.frame_inputs,
            width=520,
        )
        self.entry_folder.grid(row=7, column=0, padx=5,
                               pady=(0, 10), sticky="w")
        self.entry_folder.insert(0, self.download_root)
        self.entry_folder.configure(state="disabled")

        self.button_choose_folder = self.make_button(
            self.frame_inputs,
            text=self.ui("Выбрать", "Choose"),
            width=120,
            command=self.choose_download_folder,
        )
        self.button_choose_folder.grid(
            row=7, column=1, padx=(5, 5), pady=(0, 10), sticky="e")

        # ---------- Кнопки управления и прогресс ----------
        self.frame_controls = ctk.CTkFrame(self)
        self.frame_controls.pack(pady=(5, 10))

        self.button_download = self.make_button(
            self.frame_controls,
            text=self.ui("Скачать фото", "Download photos"),
            command=self.start_download_thread,
            width=200,
            height=40,
        )
        self.button_download.grid(row=0, column=0, padx=(0, 10))

        self.button_pause = self.make_button(
            self.frame_controls,
            text=self.ui("Пауза", "Pause"),
            width=90,
            command=self.toggle_pause,
        )
        self.button_pause.grid(row=0, column=1, padx=(0, 5))

        self.button_stop = self.make_button(
            self.frame_controls,
            text=self.ui("Стоп", "Stop"),
            width=90,
            command=self.stop_download,
        )
        self.button_stop.grid(row=0, column=2, padx=(0, 0))

        self.button_pause.configure(state="disabled")
        self.button_stop.configure(state="disabled")

        self.progress = ctk.CTkProgressBar(self, mode="determinate")
        self.progress.pack(fill="x", padx=20)
        self.progress.set(0)

        self.label_progress_info = ctk.CTkLabel(
            self,
            text=self.ui("Ожидание...", "Waiting..."),
            font=ctk.CTkFont(size=12),
        )
        self.label_progress_info.pack(pady=(5, 5))

        self.label_preview = ctk.CTkLabel(
            self,
            text="",
            width=200,
            height=150,
        )
        self.label_preview.pack(pady=(5, 5))

        self.text_log = ctk.CTkTextbox(self, height=100)
        self.text_log.pack(fill="both", expand=True, padx=20, pady=(5, 15))
        self.text_log.insert(
            "end",
            f"{APP_NAME} {self.ui('готов к работе', 'is ready')}.\n",
        )
        self.text_log.configure(state="disabled")

        self.last_download_dir = None

        self.stop_flag = False
        self.pause_flag = False

    # ===== Вспомогательные методы GUI =====

    def ui(self, ru_text: str, en_text: str) -> str:
        return ru_text if self.cyrillic_ui else en_text

    def make_button(self, parent, text: str, command, width: int, height: int | None = None):
        if self.cyrillic_ui:
            kwargs = {
                "text": text,
                "width": width,
                "command": command,
            }
            if height is not None:
                kwargs["height"] = height
            return ctk.CTkButton(parent, **kwargs)

        kwargs = {
            "text": text,
            "command": command,
            "width": max(8, width // 10),
            "bg": "#1f6aa5",
            "fg": "white",
            "activebackground": "#144870",
            "activeforeground": "white",
            "disabledforeground": "#9ca3af",
            "relief": "raised",
            "bd": 1,
            "highlightthickness": 0,
        }
        if height is not None:
            kwargs["height"] = 2 if height >= 36 else 1
        return tk.Button(parent, **kwargs)

    def paste_into_entry(self, entry):
        try:
            text = self.clipboard_get()
            try:
                entry.delete("sel.first", "sel.last")
            except Exception:
                pass
            entry.insert(entry.index("insert"), text)
            entry.focus_set()
        except Exception as e:
            self.log(self.ui(
                f"[WARN] Не удалось вставить из буфера обмена: {e}",
                f"[WARN] Failed to paste from clipboard: {e}",
            ))
        return "break"

    def bind_entry_shortcuts(self, entry):
        paste_keysyms = {"v", "V", "Cyrillic_em"}
        copy_keysyms = {"c", "C", "Cyrillic_es"}
        select_all_keysyms = {"a", "A", "Cyrillic_ef"}

        def paste(_event=None):
            return self.paste_into_entry(entry)

        def copy(_event=None):
            try:
                try:
                    text = entry.selection_get()
                except Exception:
                    text = entry.get()
                self.clipboard_clear()
                self.clipboard_append(text)
            except Exception:
                pass
            return "break"

        def select_all(_event=None):
            try:
                entry.select_range(0, "end")
                entry.icursor("end")
            except Exception:
                pass
            return "break"

        def control_key_handler(event):
            keysym = getattr(event, "keysym", "")
            keycode = getattr(event, "keycode", None)

            # X11 keycodes for the physical V/C/A keys are 55/54/38.
            # They keep working when the active layout produces Cyrillic keysyms.
            if keysym in paste_keysyms or keycode == 55:
                return paste(event)
            if keysym in copy_keysyms or keycode == 54:
                return copy(event)
            if keysym in select_all_keysyms or keycode == 38:
                return select_all(event)
            return None

        for sequence in ("<Control-v>", "<Control-V>", "<Shift-Insert>", "<<Paste>>"):
            entry.bind(sequence, paste)
        for sequence in ("<Control-c>", "<Control-C>", "<Control-Insert>", "<<Copy>>"):
            entry.bind(sequence, copy)
        for sequence in ("<Control-a>", "<Control-A>"):
            entry.bind(sequence, select_all)
        entry.bind("<Control-KeyPress>", control_key_handler)

    def choose_download_folder(self):
        initial_dir = self.download_root if os.path.isdir(
            self.download_root) else os.getcwd()
        selected = filedialog.askdirectory(
            parent=self,
            initialdir=initial_dir,
            title=self.ui("Выберите папку для скачивания",
                          "Choose download folder"),
        )
        if not selected:
            return

        self.download_root = selected
        self.config_data["download_root"] = selected
        save_config(self.config_data)

        self.entry_folder.configure(state="normal")
        self.entry_folder.delete(0, "end")
        self.entry_folder.insert(0, selected)
        self.entry_folder.configure(state="disabled")

    def log(self, msg: str):
        self.text_log.configure(state="normal")
        self.text_log.insert("end", msg + "\n")
        self.text_log.see("end")
        self.text_log.configure(state="disabled")
        self.update_idletasks()

    def update_progress_label(self, text: str):
        self.label_progress_info.configure(text=text)
        self.update_idletasks()

    def update_preview(self, filepath: str):
        try:
            img = Image.open(filepath)
            img.thumbnail((200, 150), Image.Resampling.LANCZOS)
            photo = ctk.CTkImage(
                light_image=img, dark_image=img, size=img.size)
            self.label_preview.configure(image=photo, text="")
            self.label_preview.image = photo
        except Exception as e:
            self.log(self.ui(
                f"[WARN] Не удалось загрузить превью: {e}",
                f"[WARN] Failed to load preview: {e}",
            ))

    def open_token_page(self):
        webbrowser.open("https://vkhost.github.io", new=2)
        self.log(self.ui(
            "[INFO] Открыта страница для получения токена. Скопируйте всю ссылку после авторизации.",
            "[INFO] Token page opened. Copy the full URL after authorization.",
        ))

    def open_folder(self, path: str):
        if os.path.exists(path):
            if os.name == "nt":
                os.startfile(path)
            elif os.name == "posix":
                subprocess.Popen(["xdg-open", path])

    def show_completion_dialog(self, downloaded: int, total: int, folder: str):
        dialog = ctk.CTkToplevel(self)
        dialog.title(self.ui("Скачивание завершено", "Download complete"))
        dialog.geometry("400x200")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (dialog.winfo_width() // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        label_title = ctk.CTkLabel(
            dialog,
            text=self.ui("✅ Скачивание завершено!", "Download complete!"),
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        label_title.pack(pady=(20, 10))

        label_info = ctk.CTkLabel(
            dialog,
            text=self.ui(
                f"Скачано файлов: {downloaded} из {total}",
                f"Files downloaded: {downloaded} of {total}",
            ),
            font=ctk.CTkFont(size=14),
        )
        label_info.pack(pady=(0, 5))

        label_folder = ctk.CTkLabel(
            dialog,
            text=self.ui(
                f"Папка: {os.path.basename(folder)}",
                f"Folder: {os.path.basename(folder)}",
            ),
            font=ctk.CTkFont(size=12),
            wraplength=360,
        )
        label_folder.pack(pady=(0, 20))

        frame_buttons = ctk.CTkFrame(dialog)
        frame_buttons.pack(pady=(5, 15))

        button_open = self.make_button(
            frame_buttons,
            text=self.ui("Открыть папку", "Open folder"),
            width=150,
            command=lambda: (self.open_folder(folder), dialog.destroy()),
        )
        button_open.grid(row=0, column=0, padx=5)

        button_close = self.make_button(
            frame_buttons,
            text=self.ui("Закрыть", "Close"),
            width=150,
            command=dialog.destroy,
        )
        button_close.grid(row=0, column=1, padx=5)

    def toggle_pause(self):
        self.pause_flag = not self.pause_flag
        if self.pause_flag:
            self.update_progress_label(self.ui("Пауза...", "Paused..."))
            self.button_pause.configure(text=self.ui("Продолжить", "Resume"))
        else:
            self.update_progress_label(
                self.ui("Продолжаю загрузку...", "Resuming download..."))
            self.button_pause.configure(text=self.ui("Пауза", "Pause"))

    def stop_download(self):
        self.stop_flag = True
        self.pause_flag = False
        self.update_progress_label(self.ui(
            "Остановка по запросу пользователя...",
            "Stopping by user request...",
        ))

    def start_download_thread(self):
        t = threading.Thread(target=self.download_photos)
        t.daemon = True
        t.start()

    # ===== Основная логика скачивания =====

    def download_photos(self):
        self.stop_flag = False
        self.pause_flag = False

        token_raw = self.entry_token.get().strip()
        peer_raw = self.entry_peer.get().strip()

        token = extract_token(token_raw)
        if not token:
            self.log(self.ui(
                "[ERR] Не удалось извлечь access token. Вставьте токен или полную ссылку после авторизации.",
                "[ERR] Could not extract access token. Paste a token or the full URL after authorization.",
            ))
            self.update_progress_label(
                self.ui("Ошибка: токен не найден", "Error: token not found"))
            return

        try:
            peer_id = int(peer_raw)
        except ValueError:
            self.log(self.ui(
                "[ERR] peer_id должен быть числом.",
                "[ERR] peer_id must be a number.",
            ))
            self.update_progress_label(
                self.ui("Ошибка: неверный peer_id", "Error: invalid peer_id"))
            return

        self.config_data["last_token"] = token
        self.config_data["last_peer_id"] = peer_id
        self.config_data["download_root"] = self.download_root
        save_config(self.config_data)

        try:
            vk_session = vk_api.VkApi(token=token)
            vk = vk_session.get_api()
        except Exception as e:
            self.log(self.ui(
                f"[ERR] Не удалось создать сессию VK: {e}",
                f"[ERR] Failed to create VK session: {e}",
            ))
            self.update_progress_label(
                self.ui("Ошибка подключения к VK", "VK connection error"))
            return

        try:
            name = get_user_name(vk, peer_id)
        except Exception as e:
            self.log(self.ui(
                f"[WARN] Не удалось получить имя пользователя: {e}",
                f"[WARN] Failed to get user name: {e}",
            ))
            name = f"id{peer_id}"

        safe_name = sanitize(name)
        download_root = self.download_root or os.getcwd()
        ensure_dir(download_root)
        download_dir = os.path.join(
            download_root, f"download_{safe_name}_id{peer_id}")
        ensure_dir(download_dir)
        self.last_download_dir = os.path.abspath(download_dir)
        manifest = load_download_manifest(download_dir)
        used_names = set(os.listdir(download_dir))

        self.log(self.ui(
            f"Папка для сохранения: {download_dir}",
            f"Save folder: {download_dir}",
        ))
        self.log(self.ui(
            f"Начинаю скачивание из peer_id={peer_id}...",
            f"Starting download from peer_id={peer_id}...",
        ))
        self.button_download.configure(state="disabled")
        self.button_pause.configure(state="normal")
        self.button_stop.configure(state="normal")
        self.progress.set(0)
        self.update_progress_label(self.ui("Подключение...", "Connecting..."))

        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=DOWNLOAD_WORKERS,
            pool_maxsize=DOWNLOAD_WORKERS
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        next_from = None
        counter = 0
        total = 0

        download_tasks = []
        scanned_attachments = 0

        start_time = time.time()

        try:
            # Фаза 1: собираем список всех файлов
            self.update_progress_label(
                self.ui("Получение списка файлов...", "Fetching file list..."))

            while True:
                if self.stop_flag:
                    self.log(self.ui(
                        "[INFO] Остановка до начала скачивания (по запросу пользователя).",
                        "[INFO] Stopped before download by user request.",
                    ))
                    self.update_progress_label(
                        self.ui("Остановлено.", "Stopped."))
                    return

                while self.pause_flag and not self.stop_flag:
                    time.sleep(0.1)

                params = {
                    "peer_id": peer_id,
                    "media_type": "photo",
                    "count": 200,
                    "photo_sizes": 1,
                }
                if next_from:
                    params["start_from"] = next_from

                resp = vk.messages.getHistoryAttachments(**params)
                items = resp.get("items", [])
                if not items:
                    break

                scanned_attachments += len(items)
                self.update_progress_label(
                    self.ui(
                        f"Получение списка файлов... просмотрено вложений: ~{scanned_attachments}",
                        f"Fetching file list... scanned attachments: ~{scanned_attachments}",
                    )
                )

                for item in items:
                    if self.stop_flag:
                        self.log(self.ui(
                            "[INFO] Остановка при просмотре вложений.",
                            "[INFO] Stopped while scanning attachments.",
                        ))
                        self.update_progress_label(
                            self.ui("Остановлено.", "Stopped."))
                        return

                    while self.pause_flag and not self.stop_flag:
                        time.sleep(0.1)

                    attachment = item.get("attachment", {})
                    if attachment.get("type") != "photo":
                        continue

                    photo = attachment.get("photo", {})
                    url = get_biggest_photo_url(photo)
                    if not url:
                        continue

                    photo_key = make_photo_key(photo)
                    existing_filename = manifest.get(photo_key)
                    if existing_filename:
                        filepath = os.path.join(
                            download_dir, existing_filename)
                        if os.path.exists(filepath):
                            counter += 1
                            continue

                    filename, filepath = allocate_photo_path(
                        download_dir,
                        photo.get("date", 0),
                        used_names,
                    )

                    download_tasks.append((url, filepath, filename, photo_key))

                next_from = resp.get("next_from")
                if not next_from:
                    break

                time.sleep(DELAY)

            total = counter + len(download_tasks)
            self.log(self.ui(
                f"Файлов к учёту (уже есть + нужно скачать): {total}",
                f"Files counted (existing + to download): {total}",
            ))

            if not download_tasks:
                self.log(self.ui(
                    "Все файлы уже скачаны, ничего нового нет.",
                    "All files are already downloaded; nothing new to fetch.",
                ))
                self.progress.set(1.0)
                self.update_progress_label(
                    self.ui(
                        f"Завершено: {counter}/{total} файлов",
                        f"Complete: {counter}/{total} files",
                    ))
                self.after(500, lambda: self.show_completion_dialog(
                    counter, total, self.last_download_dir))
                play_notification_sound()
                return

            self.log(self.ui(
                f"Начинаю параллельное скачивание {len(download_tasks)} файлов ({DOWNLOAD_WORKERS} потоков)...",
                f"Starting parallel download of {len(download_tasks)} files ({DOWNLOAD_WORKERS} workers)...",
            ))

            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
                futures = {
                    executor.submit(download_file_task, url, filepath, session, self): (filepath, filename, photo_key)
                    for url, filepath, filename, photo_key in download_tasks
                }

                for future in as_completed(futures):
                    while self.pause_flag and not self.stop_flag:
                        time.sleep(0.2)

                    if self.stop_flag:
                        self.log(self.ui(
                            "[INFO] Загрузка остановлена пользователем (ожидаю завершения активных потоков).",
                            "[INFO] Download stopped by user; waiting for active workers.",
                        ))
                        self.update_progress_label(self.ui(
                            "Остановлено пользователем.",
                            "Stopped by user.",
                        ))
                        break

                    filepath, filename, photo_key = futures[future]
                    file_path_result, success, error = future.result()

                    if success:
                        self.log(f"[DL] {filename}")
                        counter += 1
                        manifest[photo_key] = filename
                        save_download_manifest(download_dir, manifest)
                        self.after(
                            0, lambda fp=file_path_result: self.update_preview(fp))
                    else:
                        self.log(self.ui(
                            f"[ERR] Не удалось скачать {filename}: {error}",
                            f"[ERR] Failed to download {filename}: {error}",
                        ))

                    if total > 0:
                        progress_val = counter / total
                        self.progress.set(progress_val)

                        elapsed = time.time() - start_time
                        if counter > 0:
                            avg_time = elapsed / counter
                            remaining = max(0, (total - counter) * avg_time)
                            eta_str = format_time(remaining, self.cyrillic_ui)
                        else:
                            eta_str = self.ui("расчёт...", "calculating...")

                        self.update_progress_label(
                            self.ui(
                                f"Скачано: {counter}/{total} | Осталось: ~{eta_str}",
                                f"Downloaded: {counter}/{total} | Remaining: ~{eta_str}",
                            ))

            if not self.stop_flag:
                self.progress.set(1.0)
                elapsed_total = time.time() - start_time
                self.update_progress_label(
                    self.ui(
                        f"Завершено: {counter}/{total} файлов за {format_time(elapsed_total, self.cyrillic_ui)}",
                        f"Complete: {counter}/{total} files in {format_time(elapsed_total, self.cyrillic_ui)}",
                    ))
                self.log(self.ui(
                    f"Готово, скачано файлов: {counter}",
                    f"Done, downloaded files: {counter}",
                ))
                play_notification_sound()
                self.after(500, lambda: self.show_completion_dialog(
                    counter, total, self.last_download_dir))
            else:
                self.log(self.ui(
                    f"[INFO] Остановлено. Успели обработать файлов: {counter} из {total}",
                    f"[INFO] Stopped. Processed {counter} of {total} files.",
                ))

        except Exception as e:
            self.log(self.ui(
                f"[ERR] Общая ошибка: {e}",
                f"[ERR] General error: {e}",
            ))
            self.update_progress_label(
                self.ui("Ошибка при скачивании", "Download error"))
        finally:
            session.close()
            self.button_download.configure(state="normal")
            self.button_pause.configure(
                state="disabled", text=self.ui("Пауза", "Pause"))
            self.button_stop.configure(state="disabled")
            self.pause_flag = False


if __name__ == "__main__":
    app = App()
    app.mainloop()

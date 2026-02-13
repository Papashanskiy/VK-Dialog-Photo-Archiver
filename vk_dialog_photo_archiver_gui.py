import os
import time
import threading
import webbrowser
import subprocess
import json
import winsound
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import vk_api
import customtkinter as ctk
from PIL import Image

APP_NAME = "VK Dialog Photo Archiver"
APP_VERSION = "1.0"
CONFIG_FILE = "config.json"
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


def format_time(seconds: float) -> str:
    """Форматирует секунды в читаемый вид: '1м 23с' или '45с'."""
    if seconds < 60:
        return f"{int(seconds)}с"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}м {secs}с"


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
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Загружаем конфиг
        self.config_data = load_config()

        last_token = self.config_data.get("last_token")
        self._initial_token = last_token or ""

        # Настройки внешнего вида
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("640x640")
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
            text="Выгружает все фото из диалога ВКонтакте в локальную папку.",
            font=ctk.CTkFont(size=13),
            wraplength=600,
        )
        self.label_sub.pack(pady=(0, 5))

        self.label_author = ctk.CTkLabel(
            self,
            text=f"Автор: {APP_AUTHOR}",
            font=ctk.CTkFont(size=11),
        )
        self.label_author.pack(pady=(0, 10))

        # ---------- Поля ввода ----------
        self.frame_inputs = ctk.CTkFrame(self)
        self.frame_inputs.pack(fill="x", padx=20, pady=10)

        # Токен
        self.label_token = ctk.CTkLabel(self.frame_inputs, text="Access token:")
        self.label_token.grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=(10, 5))

        self.entry_token = ctk.CTkEntry(
            self.frame_inputs,
            width=420,
            show="*",
            placeholder_text="Вставьте токен или полную ссылку после авторизации",
        )
        self.entry_token.grid(row=1, column=0, padx=5, pady=(0, 8), sticky="w")

        if self._initial_token:
            self.entry_token.insert(0, self._initial_token)

        # Горячие клавиши для токена
        def _token_key_handler(event):
            code = event.keycode
            ctrl = (event.state & 0x4) != 0

            if not ctrl:
                return

            if code == 86:  # Ctrl+V
                try:
                    text = self.clipboard_get()
                    pos = self.entry_token.index("insert")
                    self.entry_token.insert(pos, text)
                except Exception:
                    pass
                return "break"

            if code == 67:  # Ctrl+C
                try:
                    text = self.entry_token.get()
                    self.clipboard_clear()
                    self.clipboard_append(text)
                except Exception:
                    pass
                return "break"

            if code == 65:  # Ctrl+A
                self.entry_token.select_range(0, "end")
                self.entry_token.icursor("end")
                return "break"

        self.entry_token.bind("<KeyPress>", _token_key_handler)

        # Кнопка "Получить токен"
        self.button_get_token = ctk.CTkButton(
            self.frame_inputs,
            text="Получить токен",
            width=140,
            command=self.open_token_page,
        )
        self.button_get_token.grid(row=1, column=1, padx=(5, 5), pady=(0, 8), sticky="e")

        self.label_token_help = ctk.CTkLabel(
            self.frame_inputs,
            text="Нажмите кнопку выше, авторизуйтесь (права: messages, photos, offline), скопируйте всю ссылку из адресной строки.",
            font=ctk.CTkFont(size=11),
            wraplength=560,
        )
        self.label_token_help.grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 10))

        # peer_id
        self.label_peer = ctk.CTkLabel(self.frame_inputs, text="peer_id диалога:")
        self.label_peer.grid(row=3, column=0, sticky="w", padx=5, pady=(5, 5))

        self.entry_peer = ctk.CTkEntry(
            self.frame_inputs,
            width=220,
            placeholder_text="например, 12345678",
        )
        self.entry_peer.grid(row=4, column=0, sticky="w", padx=5, pady=(0, 5))

        def _peer_key_handler(event):
            code = event.keycode
            ctrl = (event.state & 0x4) != 0

            if not ctrl:
                return

            if code == 86:  # Ctrl+V
                try:
                    text = self.clipboard_get()
                    pos = self.entry_peer.index("insert")
                    self.entry_peer.insert(pos, text)
                except Exception:
                    pass
                return "break"

            if code == 67:  # Ctrl+C
                try:
                    text = self.entry_peer.get()
                    self.clipboard_clear()
                    self.clipboard_append(text)
                except Exception:
                    pass
                return "break"

            if code == 65:  # Ctrl+A
                self.entry_peer.select_range(0, "end")
                self.entry_peer.icursor("end")
                return "break"

        self.entry_peer.bind("<KeyPress>", _peer_key_handler)

        self.label_peer_help = ctk.CTkLabel(
            self.frame_inputs,
            text="ЛС: id пользователя. Беседа: 2000000000 + chat_id.",
            font=ctk.CTkFont(size=11),
        )
        self.label_peer_help.grid(row=5, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 10))

        # ---------- Кнопки управления и прогресс ----------
        self.frame_controls = ctk.CTkFrame(self)
        self.frame_controls.pack(pady=(5, 10))

        self.button_download = ctk.CTkButton(
            self.frame_controls,
            text="Скачать фото",
            command=self.start_download_thread,
            width=200,
            height=40,
        )
        self.button_download.grid(row=0, column=0, padx=(0, 10))

        self.button_pause = ctk.CTkButton(
            self.frame_controls,
            text="Пауза",
            width=90,
            command=self.toggle_pause,
        )
        self.button_pause.grid(row=0, column=1, padx=(0, 5))

        self.button_stop = ctk.CTkButton(
            self.frame_controls,
            text="Стоп",
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
            text="Ожидание...",
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
        self.text_log.insert("end", f"{APP_NAME} готов к работе.\n")
        self.text_log.configure(state="disabled")

        self.last_download_dir = None

        self.stop_flag = False
        self.pause_flag = False

    # ===== Вспомогательные методы GUI =====

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
            photo = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.label_preview.configure(image=photo, text="")
            self.label_preview.image = photo
        except Exception as e:
            self.log(f"[WARN] Не удалось загрузить превью: {e}")

    def open_token_page(self):
        webbrowser.open("https://vkhost.github.io", new=2)
        self.log("[INFO] Открыта страница для получения токена. Скопируйте всю ссылку после авторизации.")

    def open_folder(self, path: str):
        if os.path.exists(path):
            if os.name == "nt":
                os.startfile(path)
            elif os.name == "posix":
                subprocess.Popen(["xdg-open", path])

    def show_completion_dialog(self, downloaded: int, total: int, folder: str):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Скачивание завершено")
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
            text="✅ Скачивание завершено!",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        label_title.pack(pady=(20, 10))

        label_info = ctk.CTkLabel(
            dialog,
            text=f"Скачано файлов: {downloaded} из {total}",
            font=ctk.CTkFont(size=14),
        )
        label_info.pack(pady=(0, 5))

        label_folder = ctk.CTkLabel(
            dialog,
            text=f"Папка: {os.path.basename(folder)}",
            font=ctk.CTkFont(size=12),
            wraplength=360,
        )
        label_folder.pack(pady=(0, 20))

        frame_buttons = ctk.CTkFrame(dialog)
        frame_buttons.pack(pady=(5, 15))

        button_open = ctk.CTkButton(
            frame_buttons,
            text="Открыть папку",
            width=150,
            command=lambda: (self.open_folder(folder), dialog.destroy()),
        )
        button_open.grid(row=0, column=0, padx=5)

        button_close = ctk.CTkButton(
            frame_buttons,
            text="Закрыть",
            width=150,
            command=dialog.destroy,
        )
        button_close.grid(row=0, column=1, padx=5)

    def toggle_pause(self):
        self.pause_flag = not self.pause_flag
        if self.pause_flag:
            self.update_progress_label("Пауза...")
            self.button_pause.configure(text="Продолжить")
        else:
            self.update_progress_label("Продолжаю загрузку...")
            self.button_pause.configure(text="Пауза")

    def stop_download(self):
        self.stop_flag = True
        self.pause_flag = False
        self.update_progress_label("Остановка по запросу пользователя...")

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
            self.log("[ERR] Не удалось извлечь access token. Вставьте токен или полную ссылку после авторизации.")
            self.update_progress_label("Ошибка: токен не найден")
            return

        self.config_data["last_token"] = token
        save_config(self.config_data)

        try:
            peer_id = int(peer_raw)
        except ValueError:
            self.log("[ERR] peer_id должен быть числом.")
            self.update_progress_label("Ошибка: неверный peer_id")
            return

        try:
            vk_session = vk_api.VkApi(token=token)
            vk = vk_session.get_api()
        except Exception as e:
            self.log(f"[ERR] Не удалось создать сессию VK: {e}")
            self.update_progress_label("Ошибка подключения к VK")
            return

        try:
            name = get_user_name(vk, peer_id)
        except Exception as e:
            self.log(f"[WARN] Не удалось получить имя пользователя: {e}")
            name = f"id{peer_id}"

        safe_name = sanitize(name)
        download_dir = f"download_{safe_name}_id{peer_id}"
        ensure_dir(download_dir)
        self.last_download_dir = os.path.abspath(download_dir)

        self.log(f"Папка для сохранения: {download_dir}")
        self.log(f"Начинаю скачивание из peer_id={peer_id}...")
        self.button_download.configure(state="disabled")
        self.button_pause.configure(state="normal")
        self.button_stop.configure(state="normal")
        self.progress.set(0)
        self.update_progress_label("Подключение...")

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
            self.update_progress_label("Получение списка файлов...")

            while True:
                if self.stop_flag:
                    self.log("[INFO] Остановка до начала скачивания (по запросу пользователя).")
                    self.update_progress_label("Остановлено.")
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
                    f"Получение списка файлов... просмотрено вложений: ~{scanned_attachments}"
                )

                for item in items:
                    if self.stop_flag:
                        self.log("[INFO] Остановка при просмотре вложений.")
                        self.update_progress_label("Остановлено.")
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

                    owner_id = photo.get("owner_id", 0)
                    photo_id = photo.get("id", 0)
                    date = photo.get("date", 0)

                    filename = f"photo_{owner_id}_{photo_id}_{date}.jpg"
                    filepath = os.path.join(download_dir, filename)

                    if os.path.exists(filepath):
                        counter += 1
                        continue

                    download_tasks.append((url, filepath, filename))

                next_from = resp.get("next_from")
                if not next_from:
                    break

                time.sleep(DELAY)

            total = counter + len(download_tasks)
            self.log(f"Файлов к учёту (уже есть + нужно скачать): {total}")

            if not download_tasks:
                self.log("Все файлы уже скачаны, ничего нового нет.")
                self.progress.set(1.0)
                self.update_progress_label(f"Завершено: {counter}/{total} файлов")
                self.after(500, lambda: self.show_completion_dialog(counter, total, self.last_download_dir))
                play_notification_sound()
                return

            self.log(f"Начинаю параллельное скачивание {len(download_tasks)} файлов ({DOWNLOAD_WORKERS} потоков)...")

            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
                futures = {
                    executor.submit(download_file_task, url, filepath, session, self): (filepath, filename)
                    for url, filepath, filename in download_tasks
                }

                for future in as_completed(futures):
                    while self.pause_flag and not self.stop_flag:
                        time.sleep(0.2)

                    if self.stop_flag:
                        self.log("[INFO] Загрузка остановлена пользователем (ожидаю завершения активных потоков).")
                        self.update_progress_label("Остановлено пользователем.")
                        break

                    filepath, filename = futures[future]
                    file_path_result, success, error = future.result()

                    if success:
                        self.log(f"[DL] {filename}")
                        counter += 1
                        self.after(0, lambda fp=file_path_result: self.update_preview(fp))
                    else:
                        self.log(f"[ERR] Не удалось скачать {filename}: {error}")

                    if total > 0:
                        progress_val = counter / total
                        self.progress.set(progress_val)

                        elapsed = time.time() - start_time
                        if counter > 0:
                            avg_time = elapsed / counter
                            remaining = max(0, (total - counter) * avg_time)
                            eta_str = format_time(remaining)
                        else:
                            eta_str = "расчёт..."

                        self.update_progress_label(f"Скачано: {counter}/{total} | Осталось: ~{eta_str}")

            if not self.stop_flag:
                self.progress.set(1.0)
                elapsed_total = time.time() - start_time
                self.update_progress_label(f"Завершено: {counter}/{total} файлов за {format_time(elapsed_total)}")
                self.log(f"Готово, скачано файлов: {counter}")
                play_notification_sound()
                self.after(500, lambda: self.show_completion_dialog(counter, total, self.last_download_dir))
            else:
                self.log(f"[INFO] Остановлено. Успели обработать файлов: {counter} из {total}")

        except Exception as e:
            self.log(f"[ERR] Общая ошибка: {e}")
            self.update_progress_label("Ошибка при скачивании")
        finally:
            session.close()
            self.button_download.configure(state="normal")
            self.button_pause.configure(state="disabled", text="Пауза")
            self.button_stop.configure(state="disabled")
            self.pause_flag = False


if __name__ == "__main__":
    app = App()
    app.mainloop()

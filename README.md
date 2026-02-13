# VK DPA | VK Dialog Photo Archiver

[![Release](https://img.shields.io/github/v/release/ItsLouan/VK-Dialog-Photo-Archiver)](https://github.com/ItsLouan/VK-Dialog-Photo-Archiver/releases)
[![License](https://img.shields.io/github/license/ItsLouan/VK-Dialog-Photo-Archiver)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org/)

**VK Dialog Photo Archiver** — утилита на Python для локальной выгрузки всех фотографий из диалога ВКонтакте в удобную папку с именем собеседника.

Автор: **ItsLouan**

---

## Возможности

- Авторизация по user access token (без логина и пароля).
- Выгрузка всех фото из указанного диалога или беседы VK.
- Использование метода `messages.getHistoryAttachments` с пагинацией по `next_from`.
- Автоматическое создание папки формата  
  `download_<Фамилия>_<Имя>_id<peer_id>`.
- Современный графический интерфейс на **CustomTkinter**:
  - тёмная тема;
  - поле для токена и `peer_id`;
  - прогресс‑бар;
  - текстовый лог хода скачивания.

---

## Требования

- Windows / Linux / macOS.
- Python **3.10+**.
- Установленные зависимости:

```bash
pip install -r requirements.txt
```

(см. `requirements.txt` в репозитории.)

---

## Получение access token

Для работы нужен **user access token** с правами `messages`, `photos`, `offline`.

1. Откройте в браузере:  
   `https://vkhost.github.io`.  
2. Выберите любое подходящее приложение (например, VK Admin).
3. При запросе прав убедитесь, что включены:
   - `messages`;
   - `photos`;
   - `offline`.
4. Подтвердите доступ.
5. После редиректа в адресной строке найдите параметр `access_token`.  
   Скопируйте **строку между** `access_token=` и `&expires_in=` — это и есть ваш токен.

Храните токен в секрете, не публикуйте его в открытых репозиториях.

---

## Как узнать peer_id

- **Личный диалог с пользователем**:  
  откройте диалог в веб‑версии VK — ссылка вида  
  `https://vk.com/im?sel=12345678` → `peer_id = 12345678`.

- **Беседа**:  
  ссылка вида `https://vk.com/im?sel=c35` → `chat_id = 35` →  
  `peer_id = 2000000000 + 35 = 2000000035`.

---

## Установка и запуск GUI

📦 **Готовый EXE для Windows** можно скачать в разделе [Releases](https://github.com/ItsLouan/VK-Dialog-Photo-Archiver/releases/latest).

1. Клонируйте репозиторий:

```bash
git clone https://github.com/ItsLouan/vk_dialog_photo_archiver.git
cd vk_dialog_photo_archiver
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Запустите графический интерфейс:

```bash
python src/vk_dialog_photo_archiver_gui.py
```

4. В открывшемся окне:

   1. Вставьте `access token` в соответствующее поле.
   2. Укажите `peer_id` диалога или беседы.
   3. Нажмите кнопку **«Скачать фото»**.
   4. Наблюдайте прогресс‑бар и лог в нижней части окна.

Фотографии будут сохранены в подпапку формата:

```text
download_<Фамилия>_<Имя>_id<peer_id>
```

Папка создаётся **рядом с файлом запуска** (обычно в каталоге `src` или в рабочей директории, из которой вы запускали команду).

---

## Технические детали

- Для работы с VK используется библиотека `vk_api` (обёртка над официальным VK API).
- Выгрузка фото осуществляется методом `messages.getHistoryAttachments` с параметрами:
  - `media_type=photo`;
  - `count=200`;
  - `photo_sizes=1`;
  - цикл по `next_from` до конца истории.
- Для получения имени и фамилии собеседника вызывается `users.get`, после чего формируется безопасное имя папки.

---

## Дисклеймер

Проект предназначен **исключительно для личного использования**:

- соблюдайте правила и условия использования VK;
- уважайте приватность других пользователей;
- не используйте утилиту для действий, нарушающих законы вашей страны.

Автор (ItsLouan) не несёт ответственности за некорректное или незаконное использование программы.

---

## Лицензия

Проект распространяется по лицензии **MIT** (см. файл `LICENSE` в репозитории).

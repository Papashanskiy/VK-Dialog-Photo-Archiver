# VK DPA | VK Dialog Photo Archiver

[![Latest release](https://img.shields.io/github/v/release/ItsLouan/VK-Dialog-Photo-Archiver?label=release)](https://github.com/ItsLouan/VK-Dialog-Photo-Archiver/releases/latest)
[![License](https://img.shields.io/github/license/ItsLouan/VK-Dialog-Photo-Archiver?label=license)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org/)
[![VK API](https://img.shields.io/badge/VK%20API-5.199-blue)](https://dev.vk.com/ru/reference)


**VK Dialog Photo Archiver** — это мощный инструмент для автоматического скачивания и архивации всех фотографий из личных диалогов или бесед ВКонтакте. Забудьте о ручном сохранении — скрипт выкачает тысячи фото за пару минут.


## ✨ Возможности

- 🚀 **Высокая скорость**: Скачивание в многопоточном режиме.
- 📂 **Умная сортировка**: Создает отдельные папки для каждого диалога с понятными названиями.
- 🔒 **Безопасность**: Работает через официальный VK API.
- 🖼 **Оригинальное качество**: Сохраняет изображения в максимально доступном разрешении.
- 💾 **Resume Support**: Пропускает уже скачанные файлы, если запустить повторно.

## 🛠 Установка

### Вариант 1: Исполняемый файл (Windows)
Просто скачайте готовый `.exe` файл из раздела [Releases](https://github.com/ItsLouan/VK-Dialog-Photo-Archiver/releases). Python устанавливать не нужно!

### Вариант 2: Запуск из исходного кода
1. **Клонируйте репозиторий:**
   ```bash
   git clone https://github.com/ItsLouan/VK-Dialog-Photo-Archiver.git
   cd VK-Dialog-Photo-Archiver
   ```

2. **Установите зависимости:**
   ```bash
   pip install -r requirements.txt
   ```

## 🚀 Использование

1. **Получите токен доступа**:
   - Перейдите на [vkhost.github.io](https://vkhost.github.io/).
   - Выберите приложение (например, "Kate Mobile" или "VK Admin").
   - Разрешите доступ и скопируйте часть URL адресной строки между `access_token=` и `&`.

2. **Запустите программу**:
   ```bash
   python main.py
   ```

3. **Следуйте инструкциям**:
   - Вставьте ваш токен.
   - Введите ID диалога (peer_id) или выберите из списка.
   - Дождитесь завершения загрузки! ☕

## ❓ Как узнать ID диалога (Peer ID)?

- **Для ЛС**: Это просто ID пользователя (например, `12345678`).
- **Для бесед**: Это число, начинающееся с `2000000000` + ID беседы (например, `2000000001`).
- *В программе также предусмотрен автоматический поиск диалогов.*

## 🤝 Contributing

Хотите улучшить проект? Мы рады любым пулл-реквестам!
1. Форкните проект.
2. Создайте ветку (`git checkout -b feature/AmazingFeature`).
3. Закоммитьте изменения (`git commit -m 'Add some AmazingFeature'`).
4. Запушьте ветку (`git push origin feature/AmazingFeature`).
5. Откройте Pull Request.

## ❗ Дисклеймер

Проект предназначен **исключительно для личного использования**:

- соблюдайте правила и условия использования VK;
- уважайте приватность других пользователей;
- не используйте утилиту для действий, нарушающих законы вашей страны.

Автор не несёт ответственности за некорректное или незаконное использование программы.

## 📄 Лицензия

Распространяется под лицензией MIT. Подробнее см. [LICENSE](LICENSE).

---
<p align="center">
  <sub>Built with ❤️ by <a href="https://github.com/ItsLouan">ItsLouan</a></sub>
</p>
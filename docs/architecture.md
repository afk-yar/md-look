# Архитектура MDLook

Документ описывает верхнеуровневую архитектуру проекта на 29 июня 2026 года.
`MDLook` — портативное Windows-приложение для просмотра и редактирования
Markdown через pywebview/WebView2.

## Назначение

MDLook открывает `.md`/`.txt` файлы в desktop-окне, рендерит их через HTML
шаблон, даёт встроенное редактирование, сохранение напрямую на диск, поиск,
single-instance поведение, системный tray, автозапуск и ассоциацию `.md`.

## Контекстная диаграмма

```
        +-----------------------+
        | Пользователь Windows  |
        +-----------+-----------+
                    |
                    | открыть .md / запустить MDLook.exe
                    v
        +-----------------------+
        | app.py                |
        | process entrypoint    |
        +-----------+-----------+
                    |
          +---------+----------+
          |                    |
          v                    v
 +------------------+   +----------------------+
 | Single-instance  |   | pystray tray icon    |
 | TCP 127.0.0.1    |   | show/open/startup    |
 | port 52845       |   | file association     |
 +--------+---------+   +-----------+----------+
          |                         |
          +------------+------------+
                       |
                       v
        +---------------------------+
        | pywebview window          |
        | Edge WebView2             |
        +-------------+-------------+
                      |
                      v
        +---------------------------+
        | temp HTML file            |
        | MDLook-template-offline   |
        | + injected Markdown       |
        | + BRIDGE_JS               |
        +-------------+-------------+
                      |
                      v
        +---------------------------+
        | JavaScript UI             |
        | read/edit/search/export   |
        +-------------+-------------+
                      |
                      | window.pywebview.api
                      v
        +---------------------------+
        | Api class in app.py       |
        | open/save/reload/export   |
        +-------------+-------------+
                      |
                      v
        +---------------------------+
        | Local filesystem          |
        | исходный .md и export HTML|
        +---------------------------+


        Сборка:

        app.py + template + icons
              |
              v
        PyInstaller MDLook.spec
              |
              v
        dist/MDLook/MDLook.exe
```

## Основные компоненты

`app.py` — всё desktop-приложение: старт процесса, IPC, окна, tray,
интеграция с Win32 registry, pywebview API и генерация временного HTML.

`MDLook-template-offline.html` — основной frontend-шаблон. В него
подставляется содержимое Markdown-файла, имя и папка документа.

`BRIDGE_JS` в `app.py` — JavaScript-мост поверх шаблона. Переопределяет
сохранение, открытие, reload, export HTML, поиск и goto target через
`window.pywebview.api`.

`Api` class в `app.py` — Python API, доступный из WebView: `save_file`,
`save_file_as`, `open_file`, `reload_file`, `save_html`.

Single-instance IPC — локальный TCP listener на `127.0.0.1:<derived-port>`.
Порт детерминированно выводится из пути исполняемого файла или dev-скрипта,
чтобы установленная копия и portable/dev-сборка не перехватывали запуски друг
друга. Второй запуск той же копии передаёт первому процессу команду `SHOW` или
`OPEN:<path>`.

Tray и shell-интеграция — `pystray` + Win32 registry для автозапуска и
ассоциации `.md` с приложением.

`MDLook.spec` — PyInstaller spec. Основной runtime artifact — папочная сборка
`dist/MDLook/MDLook.exe`.

## Поток данных

1. Пользователь открывает файл через ассоциацию `.md`, меню tray или open dialog.
2. `app.py` читает Markdown с диска.
3. `build_html()` вставляет Markdown в offline HTML template и пишет temp HTML.
4. pywebview загружает temp HTML в Edge WebView2.
5. Пользователь читает или редактирует документ в HTML UI.
6. JS вызывает методы `Api` через pywebview bridge.
7. Python пишет изменения обратно в исходный файл или экспортирует HTML.
8. При повторном запуске второй процесс передаёт путь первому через IPC.

## Границы системы

MDLook не имеет backend-сервера и не хранит собственную базу. Все данные —
локальные файлы пользователя и временные HTML-файлы процесса. Внешние
зависимости: Windows, WebView2, pywebview, pystray, Pillow и PyInstaller.

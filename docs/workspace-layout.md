# Карта репозитория (3 зоны)

Цель: **студия**, **конвейер** и **артефакты** не смешиваются в голове и в папках.

```
youtobe-maker/
├── studio/              ← веб-морда (Streamlit), сейчас на паузе
├── scripts/             ← pipeline: CLI, ИИ, ffmpeg, джобы
├── pipeline/            ← README-указатель на scripts/
├── channels/            ← контент каналов + файлы эпизодов
├── tools/               ← внешние движки (sd.exe, ComfyUI путь в .env)
├── models/              ← веса картинок (local SD)
├── config/              ← ai_settings.json
├── templates/           ← шаблоны markdown/csv
├── logs/                ← логи фоновых задач студии
├── remotion_studio/     ← альтернативная сборка (Node)
└── docs/                ← документация
```

## 1. Studio — веб

- `studio/app.py` — вся UI-логика.
- Корневой `app.py` — только прокси для совместимости.
- `START_WEB.bat` → `studio/app.py`.

## 2. Pipeline — генерация

- Код: **`scripts/`**.
- Не требует Streamlit: запуск из терминала или `BUILD_VIDEO.bat`.
- Общие пути: `scripts/project_paths.py` (`REPO_ROOT`, `CHANNELS_DIR`, …).

## 3. Артефакты — что лежит в эпизоде

Папка: `channels/<slug>/episodes/<ID_название>/`

| Слой | Файлы | Смысл |
|------|--------|--------|
| **Источник** | `script.md`, `script_ru.md`, `visual_prompts.md`, `metadata.md`, `plot_brief.txt`, `episode_config.json`, `history/` | Тексты и план; версионируем |
| **Рендер** | `images/`, `voiceover.mp3`, `voiceover_ru.mp3`, `final_video.mp4`, `thumbnail.jpg`, `review.json`, `animation/` | Бинарники; перегенерируем |
| **Служебное** | `.sync_instruction.txt`, `sync_instruction_last.txt` | Комментарий для переработки |

Бренд канала: `channels/<slug>/brand_assets/`, `channel_settings.json`.

## Дальнейшая эволюция (не обязательно сразу)

- Вынести рендер в `episodes/.../render/` — потребует правок путей в скриптах.
- Переименовать `scripts/` → `pipeline/` физически — один большой PR с тестами путей.

Сейчас достаточно: **студия отдельно**, **CLI отдельно**, **артефакты по таблице в эпизоде**.
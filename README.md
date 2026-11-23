# Озвучка сценариев

Скрипт `scripts/generate_voice.py` озвучивает эпизод для YouTube Multi-language Audio: `script.md` → `voiceover.mp3`, а при наличии `script_ru.md` — `voiceover_ru.mp3`. Нужен интернет: `edge-tts` ходит в сервис Microsoft.

## Установка

```powershell
python -m pip install edge-tts
```

В этом окружении установлена версия `edge-tts` 7.3.1.

## Запуск

```powershell
python scripts/generate_voice.py channels/channel_02_sleep_history/episodes/SH01_quiet_night/script.md
```

Готовые файлы в той же папке: `voiceover.mp3` и при `script_ru.md` — `voiceover_ru.mp3`.

Можно передать путь к папке эпизода:

```powershell
python scripts/generate_voice.py channels/channel_02_sleep_history/episodes/SH01_roman_apartment
```

## Что делает скрипт

- Удаляет `[FACT-CHECK]`, `[THEORY]` и любые комментарии в квадратных скобках.
- Удаляет символы Markdown `#`, `*`, `_`, `>`.
- Английский: `channel_02_sleep_history` — `en-US-BrianNeural`, темп `-12%`; остальные каналы — `en-US-AndrewMultilingualNeural`, `+0%`.
- Русский (`script_ru.md`): `ru-RU-DmitryNeural` (запасные `Svetlana` / `Dariya`); для канала сна темп `-12%`. Длинные сценарии режутся на части ~280 символов, между частями пауза 1.5–3 с; при сбое WebSocket — запасные голоса и `python -m edge_tts` на часть; склейка через ffmpeg.
- Вторую дорожку загружай в YouTube Studio → Subtitles/Multi-language audio → Add audio track (Russian).

## Поиск выбросов

Скрипт `scripts/scout_trends.py` читает `## Search Keywords` из `channel_config.md`, ищет на YouTube ролики за последние 60 дней (длительность `medium` и `long`, без Shorts) и оставляет каналы до 25 000 подписчиков, у которых ролик набрал хотя бы 15 000 просмотров. `Outlier_Score` = просмотры / подписчики. Если подписчиков 0, в знаменатель берётся 1. Скрытое число подписчиков отбрасывается.

Ключ лежит только в `.env`. Образец:

```powershell
Copy-Item .env.example .env
```

В `.env` впиши свой ключ: `YOUTUBE_API_KEY=...`. Файл `.env` в git не попадает.

```powershell
python -m pip install requests python-dotenv
python scripts/scout_trends.py channels/channel_01_gaming_mysteries
```

Отчёт: `channels/channel_01_gaming_mysteries/analytics_dump/latest_scout.csv`.

Один запуск — это несколько вызовов `search.list` (по 100 единиц квоты YouTube Data API): число запросов × 2 длительности × 2 сортировки. Для семи ключевых слов это 28 вызовов.

## Сборка ролика для сна

Нужны `moviepy` 2.2.1 и `imageio-ffmpeg` (ffmpeg уже внутри пакета). MoviePy 1.0.3 на текущем Python не используется.

```powershell
python -m pip install moviepy imageio-ffmpeg
```

Положи фоновые треки (дождь, огонь, космос) в `assets/ambient/`. Если там есть несколько `.mp3`, берётся первый по имени файла. Громкость фона — 8%.

В папке эпизода должны лежать `voiceover.mp3` и картинки в `images/` (`.png`, `.jpg`, `.jpeg`, `.webp`). Имена картинок задают порядок.

```powershell
python scripts/build_sleep_video.py channels/channel_02_sleep_history/episodes/SH01_roman_apartment
```

Скрипт делит озвучку поровну между кадрами, увеличивает каждый от 1.0 до 1.07 и смешивает кадры через crossfade 1.5 секунды, не укорачивая ролик. Результат: `final_video.mp4` в той же папке (1920×1080, 24 fps, `libx264`, `aac`).

## Нарезка геймплея

`yt-dlp` скачивает только кусок между таймкодами, а не весь ролик.

```powershell
python -m pip install yt-dlp
python scripts/download_clip.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --start 00:01:10 --end 00:02:00 --out clips/gameplay.mp4
```

`--start` и `--end` можно задать секундами (`70`), как `ММ:СС` или как `ЧЧ:ММ:СС`.

Если YouTube просит подтвердить вход, добавь браузер, в котором открыт аккаунт:

```powershell
python scripts/download_clip.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --start 00:01:10 --end 00:02:00 --out clips/gameplay.mp4 --cookies-from-browser edge
```

## Сборка эпизода одной командой

Двойной клик по `BUILD_VIDEO.bat` в корне проекта открывает консоль, спрашивает путь к папке эпизода или только ID (`SH01`) и не закрывает окно, пока ты его не пропустишь.

Та же цепочка из терминала:

```powershell
python scripts/make_episode.py channels/channel_02_sleep_history/episodes/SH01_roman_apartment
```

Скрипт идёт по шагам:

1. `generate_voice.py` — если нет `voiceover.mp3` или есть `script_ru.md`, но нет `voiceover_ru.mp3`. Нужен `script.md`.
2. `generate_images.py` — читает строки `PROMPT: ` в `visual_prompts.md` и сохраняет `images/01.jpg`, `images/02.jpg` и дальше в размере 1920×1080. Уже готовый номер пропускается. Цепочка API: **Cloudflare Workers AI** (`CF_ACCOUNT_ID`, `CF_API_TOKEN`) → **Hugging Face** (`HF_TOKEN`) → **Pollinations** (резерв).
3. Для `channel_02_sleep_history` запускает `build_sleep_video.py` и собирает `final_video.mp4`. Для игрового канала на этом шаге остаются озвучка и картинки.
4. `generate_thumbnail.py` — собирает `thumbnail.jpg` (1280×720) с крупным заголовком.

Промпт в `visual_prompts.md` пишется на английском, каждый на своей строке:

```text
PROMPT: A dim Roman room at night, oil lamp, dark muted colors, no text
```

## Обложки

```powershell
python scripts/generate_thumbnail.py channels/channel_02_sleep_history/episodes/SH01_roman_apartment
```

Скрипт берёт заголовок из `metadata.md` (или `backlog.csv`), рисует фон 1280×720 через Pollinations (`model=flux`, без текста на картинке), добавляет виньетку и крупный заголовок из 2–4 слов. Если Pollinations недоступен, фон берётся из `images/01.jpg`. Результат: `thumbnail.jpg` в папке эпизода.

## Радар ниш

```powershell
python scripts/discover_niches.py
```

Нужен `YOUTUBE_API_KEY` в `.env`. Скрипт ищет западный faceless-контент за 30 дней (видео длиннее 8 минут), оставляет каналы до 15 000 подписчиков, ролики от 50 000 просмотров и `Outlier_Score >= 5`. Отчёт: `market_radar/new_niches_YYYY-MM-DD.csv`.

## Веб-панель (Streamlit)

```powershell
python -m pip install streamlit pandas
```

Двойной клик по `START_WEB.bat` или:

```powershell
streamlit run app.py
```

Панель читает и пишет те же файлы, что CLI: `channels/`, `market_radar/`, вызовы `scripts/*.py`. В сайдбаре — выбор канала и создание нового из `templates/`. Разделы: бэклог и Scout, студия эпизода (редакторы, сборка, предпросмотр), радар ниш и заглушка публикации OAuth.

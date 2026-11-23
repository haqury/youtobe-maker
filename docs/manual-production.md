# Ручной конвейер (без веб-морды)

Корень репозитория: `youtobe-maker`.  
Пример эпизода:

`channels/channel_03_enchanted_realms/episodes/S01E01_the_alchemists_journey`

## Быстрый путь

```bat
BUILD_VIDEO.bat
```

Ввести путь к папке или ID `S01E01` — вызовется `scripts/make_episode.py`.

## По шагам

Из корня репозитория (`cd` в `youtobe-maker`):

```bat
set EP=channels\channel_03_enchanted_realms\episodes\S01E01_the_alchemists_journey

python scripts\generate_voice.py %EP%
python scripts\generate_images.py %EP%
python scripts\build_sleep_video.py %EP%
```

Enchanted Realms может использовать Remotion / ComfyUI — смотри `animation_settings.json` в папке эпизода и `scripts/animation_settings.py`.

## Что должно быть до сборки

| Файл | Откуда |
|------|--------|
| `script.md` | редактор / генерация |
| `visual_prompts.md` | сценарий / ИИ |
| `script_ru.md` | опционально, для RU-озвучки |
| `episode_config.json` | опционально: `voice_fade_out_sec`, `ambient_tail_sec` — концовка ролика |

Финальный рендер: голос затухает за `voice_fade_out_sec` до конца `voiceover.mp3`, затем `ambient_tail_sec` — **последний кадр** и ambient без речи. Смена кадров по-прежнему укладывается в длительность озвучки, хвост её не растягивает.

## Что появится после

| Файл | Скрипт |
|------|--------|
| `voiceover.mp3`, `voiceover_ru.mp3` | generate_voice |
| `images/*.jpg` | generate_images |
| `final_video.mp4` | build_sleep_video |

## Настройки

- `.env` — API-ключи
- `config/ai_settings.json` — провайдер текста/картинок
- `channels/<slug>/channel_settings.json` — голос, бренд, стиль промптов

Студия: `studio/README.md` (на паузе).
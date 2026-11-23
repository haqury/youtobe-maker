# Pipeline (генерация и сборка)

Исполняемый код конвейера лежит в **`scripts/`** (историческое имя).  
Эта папка — ярлык для навигации; дублировать код сюда не нужно.

| Задача | Скрипт |
|--------|--------|
| Озвучка | `generate_voice.py` |
| Картинки | `generate_images.py` |
| Сборка sleep-video | `build_sleep_video.py` |
| Remotion | `build_remotion_video.py` |
| Всё подряд | `make_episode.py`, `run_full_pipeline.py` |

Ручной режим: [docs/manual-production.md](../docs/manual-production.md)
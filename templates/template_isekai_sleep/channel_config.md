# Конфиг канала: isekai sleep language

Шаблон-пакет «обучение языку во сне / isekai comprehensible input». Выбирается в Studio при создании канала. Генератор `studio/app.py` копирует этот файл как есть и подставляет slug вместо плейсхолдера в разделе «Идентификаторы».

## Ниша

- Тема: изучение языка на слух (Comprehensible Input / TPRS), уют-исекай перед сном.
- Уровень: A1. Одно новое слово за раз, 3–4 знакомых слова вокруг.
- Формат: длинные аудио-ролики 2.5–3 часа (картинки + озвучка + тихий ambient).
- Подача: ночное путешествие от первого лица, зритель идёт за серым котом-проводником с тусклым фонарём.
- Запреты: никакого хоррора, никаких медицинских обещаний про сон («выучишь язык во сне», «лечит бессонницу»), никаких внезапных поворотов и вопросов к зрителю.

## Машинные ключи (читает `scripts/isekai_config.py`)

Строки ниже должны оставаться обычными строками `KEY: value`, вне блоков кода.

TARGET_LANG: es
NATIVE_LANG: en
MENTOR_NATIVE: en-US-AndrewNeural
MENTOR_TARGET: es-ES-AlvaroNeural
NPC_TARGET: es-ES-ElviraNeural
BASE_STYLE_PROMPT: cozy dark anime illustration, muted desaturated colors for sleep, night fantasy forest or wooden cart or tavern, a calm gray cat guide in a cloak holding a dim lantern, soft warm lantern light, chiaroscuro, no text, no neon, no horror

### Другие языки (замена голосов при смене TARGET_LANG)

Голоса edge-tts для проверки. Чтобы переключить язык, поменяй `TARGET_LANG` и две строки с голосами выше.

- fr: ментор fr-FR-HenriNeural, NPC fr-FR-DeniseNeural
- de: ментор de-DE-ConradNeural, NPC de-DE-KatjaNeural
- it: ментор it-IT-DiegoNeural, NPC it-IT-ElsaNeural

## Целевая аудитория

- Кто смотрит: взрослые, которые учат язык с нуля и слушают перед сном или фоном.
- Возраст: 18–45.
- География: англоязычные страны (родной язык зрителя — `NATIVE_LANG`).
- Боль или желание: «хочу начать понимать язык без учебника и зубрёжки».
- Что зритель должен сделать после ролика: подписаться, включить следующий эпизод.

## Тон голоса

- Спокойный, тёплый, медленный. Обращение на «ты» (second person).
- Роли-слоты: `MENTOR_TARGET_<язык>`, повтор `MENTOR_TARGET_<язык>_slow`, `MENTOR_NATIVE_<язык>`, `NPC_TARGET_<язык>`, переход `transitions_bank_<категория>`. Один смысловой ID на микро-сцену. Голоса в конфиге по-прежнему `MENTOR_NATIVE`, `MENTOR_TARGET`, `NPC_TARGET`.
- Запрещено: грамматические лекции, списки из 20 существительных, вопросы, «вдруг», «внезапно».

## Правила генерации

- Правило Cursor: `.cursor/rules/isekai_sleep_writer.mdc`.
- Сценарий только в скобочной разметке смысловых ID: `[001|MENTOR_TARGET_es|0.82]`, `[001|transitions_bank_describe_in_native|0.90]`, `[PAUSE|2.5]`, блоки `## BLOCK:`, затем `## PLAYLIST_SEQUENCE` по фазам: все `full`, потом все `target_only`, потом все `shuffle_target`.
- Политика длины: уникальные блоки короткие, акт ≈ 3–5 минут в режиме `mode=full`. `PLAYLIST_SEQUENCE` растягивает уникальный текст к 2.5–3 часам (full → target_only → shuffle_target с повторами). Пример `EP01_example` — короткий референс, а не полная ночь.
- Каждая целевая фраза сразу получает перевод на родной язык (в блоке для full).
- `visual_prompts.md`: строки `PROMPT:` — только сцена. `BASE_STYLE_PROMPT` добавляет `generate_images.py`. Каждая строка заканчивается на `16:9, no text`. Лица людей не в фокусе.
- Дедуп: перед новым эпизодом сверять `backlog.csv` и `published.csv`.
- `Expected_Views`: медиана `Views_7d` из `published.csv`. Если публикаций нет — `1000-5000` и пометка `Тест`.

## RPM

- Текущий RPM, USD:
- Целевой RPM, USD:
- Источник оценки: `published.csv` / нишевый бенчмарк / `Тест`
- Монетизация: AdSense
- Ограничения ниши: «sleep»-контент без медицинских заявлений; длинное аудио без повторяющегося спама для модерации.

## Бэклог

Допустимые значения:

- `Status`: `idea` | `research` | `scripted` | `ready` | `published` | `dropped`
- `Priority`: `P0` | `P1` | `P2`

## Идентификаторы

- Slug папки (`channels/<slug>/`): `{{SLUG}}`
- Название канала:
- URL канала:
- Дата запуска:

# Конфиг канала: isekai sleep language

Шаблон-пакет «обучение языку во сне / isekai comprehensible input». Выбирается в Studio при создании канала. Генератор `studio/app.py` копирует этот файл как есть и подставляет slug вместо плейсхолдера в разделе «Идентификаторы».

## Ниша

- Тема: изучение английского на слух (Comprehensible Input / TPRS) для русскоязычного зрителя, уют-исекай перед сном.
- Уровень: A1. Одно новое слово за раз, 3–4 знакомых слова вокруг.
- Формат: длинные аудио-ролики 2.5–3 часа (картинки + озвучка + тихий ambient).
- Подача: ночное путешествие от первого лица. В историческом плейлисте зритель идёт за спокойным местным проводником в простом тканом плаще с тусклым фонарём (со спины или вполоборота, лицо не в фокусе). Серый кот Аурелиус — только для чисто фэнтезийного плейлиста.
- Запреты: никакого хоррора, никаких медицинских обещаний про сон («выучишь язык во сне», «лечит бессонницу»), никаких внезапных поворотов и вопросов к зрителю.

## Машинные ключи (читает `scripts/isekai_config.py`)

Строки ниже должны оставаться обычными строками `KEY: value`, вне блоков кода.

TARGET_LANG: en
NATIVE_LANG: ru
MENTOR_NATIVE: ru-RU-DmitryNeural
MENTOR_TARGET: en-US-AndrewNeural
NPC_TARGET: en-US-AriaNeural
BASE_STYLE_PROMPT: cozy dark anime illustration, muted desaturated colors for sleep, night jungle or ancient stone or temple, warm rain, a small pool of dim lantern light on wet leaves or stone, no people, chiaroscuro, no text, no neon, no horror
Channel Name: LumaDream: Learn English
Handle: @LumaDreamRusLearnEnglish

### Голоса этой пары (edge-tts)

Зритель говорит по-русски и учит английский. Голос берётся из ключей выше, не из имени слота. В сценарий слоты `MENTOR_*` не писать.

- Родной рассказ: `ru-RU-DmitryNeural` — слоты `NARRATOR_NATIVE_ru`, `NARRATOR_NATIVE_ru_scene`, `NARRATOR_NATIVE_ru_lantern`, `NARRATOR_NATIVE_ru_translate`
- Изучаемый английский: `en-US-AndrewNeural` — слоты `NARRATOR_TARGET_en` и `NARRATOR_TARGET_en_repeat`
- Короткая реплика NPC на английском: `en-US-AriaNeural`

## Целевая аудитория

- Кто смотрит: русскоязычные взрослые, которые учат английский с нуля и слушают перед сном или фоном.
- Возраст: 18–45.
- География: русскоязычные страны и диаспора (родной язык зрителя — `NATIVE_LANG` = ru).
- Боль или желание: «хочу начать понимать английский без учебника и зубрёжки».
- Что зритель должен сделать после ролика: подписаться, включить следующий эпизод.

## Тон голоса

- Спокойный, тёплый, медленный. Обращение на «ты» (second person).
- Роли-слоты в сценарии: `NARRATOR_TARGET_en`, повтор `NARRATOR_TARGET_en_repeat`, `NARRATOR_NATIVE_ru` и перевод `NARRATOR_NATIVE_ru_translate`. Один смысловой ID на микро-сцену. Голоса в конфиге: `MENTOR_NATIVE`, `MENTOR_TARGET`, `NPC_TARGET`.
- Запрещено: грамматические лекции, списки из 20 существительных, вопросы, «вдруг», «внезапно».

## Правила генерации

- Правило Cursor: `.cursor/rules/isekai_sleep_writer.mdc`.
- Сценарий только в скобочной разметке смысловых ID: `[001|NARRATOR_TARGET_en|0.82]`, `[001|NARRATOR_NATIVE_ru_translate|0.90]`, `[PAUSE|2.5]`, блоки `## BLOCK:`, затем `## PLAYLIST_SEQUENCE` по фазам: все `full`, потом все `target_only`, потом все `shuffle_target`.
- Политика длины: уникальные блоки короткие, акт ≈ 3–5 минут в режиме `mode=full`. `PLAYLIST_SEQUENCE` растягивает уникальный текст к 2.5–3 часам (full → target_only → shuffle_target с повторами). Пример `EP01_example` — короткий референс, а не полная ночь.
- Каждая целевая фраза сразу получает перевод на родной язык (в блоке для full).
- `visual_prompts.md`: строки `PROMPT:` — только сцена. `BASE_STYLE_PROMPT` добавляет `generate_images.py`. Каждая строка заканчивается на `16:9, no text`. В кадре нет людей и именных проводников.
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

- Slug папки (`channels/<slug>/`): `LumaDream(rus)LearnEnglish`
- Название канала: LumaDream: Learn English
- URL канала: https://www.youtube.com/@LumaDreamRusLearnEnglish
- Дата запуска:
- Channel Name: LumaDream: Learn English
- Handle: @LumaDreamRusLearnEnglish

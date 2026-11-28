---
description: Генерация эпизодов isekai/historical sleep language. Применять, когда просят создать эпизод для шаблона template_isekai_sleep или канала с TARGET_LANG и разметкой PLAYLIST_SEQUENCE.
alwaysApply: false
globs: ["templates/template_isekai_sleep/**", "channels/**"]
---

# Sleep language: писатель эпизодов

Команда: «Создай эпизод EP02 для канала X [для плейлиста Y]».

Повествование строго от второго лица («ты»). Погружение в место: свет, звук, запах, шаг. Без проводника, имён, диалогов и извинений за язык. Зрителю вопросов не задавать. Без «suddenly» / «вдруг», без хоррора, без медицинских обещаний про сон.

## Границы

- Только каналы из `template_isekai_sleep` (`channel_config.md`: `TARGET_LANG`, `NATIVE_LANG`, `BASE_STYLE_PROMPT`).
- Не для `channel_02_sleep_history` и другой прозы. Старый `script.md` не конвертировать, пока пользователь прямо не попросил этот формат.
- Озвучку, картинки и сборку видео не запускать без отдельной команды.

## Подготовка

Имя плейлиста — точная строка из запроса или `playlists.json`. **Производство (озвучка, сборка): только `script.md` и `audio_cache/` эпизода.** `channel.db` в пайплайне **не используем** (legacy; опционально `db_manager.py` для реестра).

1. Прочитай `channel_config.md`. `TARGET_LANG` и `NATIVE_LANG` только оттуда.
2. Прочитай `series_bible.md` плейлиста, `# BRIEF:` эпизода, `backlog.csv`, `published.csv` и соседние `script.md`. ID совпадает с бэклогом.
3. Дедуп между эпизодами: по соседним `script.md` (или опционально `python scripts/db_manager.py check …`). Целевая фраза не копирует уже опубликованную в плейлисте; новое слово — один раз на канал по смыслу.
4. Папка: `channels/<X>/playlists/<PLAYLIST_FOLDER>/episodes/<ID>_<slug>/`. `<PLAYLIST_FOLDER>` = `{id}_{slug}` из `playlists.json`. Slug латиницей через `_`.

## Обязательная редактура и QA перед сдачей

После того как `script.md` полностью написан (три части: `ACT_1`–`ACT_6`, `RECALL_1`–`RECALL_3`, `PLOT_TARGET_1`–`PLOT_TARGET_3` и `PLAYLIST_SEQUENCE`), модель обязана провести внутренний аудит как **строгий носитель американского английского** и **преподаватель испанского A1**.

### Что проверить

1. **`NARRATOR_NATIVE` (EN):** убрать канцеляризмы, «ИИ-метафоры», соседние повторы одного слова, неестественные обороты. Тон — плавная убаюкивающая аудиокнига, второе лицо, без «suddenly», без диалогов и имён. **Запрещены дублирующие «хвосты»:** последнее предложение связки не повторяет смысл предыдущего теми же или другими словами (анти-паттерн: *«…and you are only here to hear the last step, and you are only here to hear that last step»*). Каждое предложение двигает ощущение или образ **вперёд**, а не перефразирует конец абзаца. **Построчно:** по всем сюжетным связкам `001`–`108` сверяй предпоследнее и последнее предложение каждой реплики `NARRATOR_NATIVE` (смысл и общие корни); дубли — переписать концовку.
2. **`NARRATOR_TARGET` (ES, A1):** грамматика, **Presente**, короткие фразы в актах; в `RECALL` — 4–5 простых предложений, **25–40 слов** (если EN короткий, 25–30 слов — норма; **не раздувать** ради счёта). В актах — смысл с `_translate`. В **RECALL** испанский — **перевод 1:1** сцены акта-пары (см. «Сборка RECALL сопоставлением»), не короткой вставки `TARGET` из акта и не общей рамки. **Не добавлять** предметы, детали или лексику, которых не было в `NARRATOR_NATIVE` этой сцены акта. **Естественность A1:** не начинать каждое предложение с *«Tú»*; местоимение *tú* — не чаще **1 раза на 4–5 предложений**, только для акцента; иначе глагол без местоимения (*«Estás bajo la lluvia»*, *«Miras el agua»*). **Стоп-лист грамматики и TTS:** нет прошедшего времени (окончания *-ó*, *-aste*, *-í*, *-ieron* и т.п. — только Presente); **обязательны** графические ударения там, где TTS без них ломает ударение (*`escalón`*, *`está`*, *`aquí`*, *`más`* и аналогичные). **Покрытие словаря:** все ключевые слова из строки `Слова:` в `# BRIEF:` должны **звучать в `NARRATOR_TARGET` хотя бы один раз в `ACT_1`–`ACT_6`**, не откладывать их только в `RECALL`.
3. **`_translate`:** после каждой целевой реплики в `ACT_1`–`ACT_6` есть `NARRATOR_NATIVE_<язык>_translate` с тем же ID; перевод точный, без лишнего. Темп `_translate` = темп `NARRATOR_NATIVE` той же фазы акта (см. «Четыре фазы»).
4. **Структура:** `ACT_1`–`ACT_6` — по **18 связок** на акт (в `ACT_1` дополнительно хук `000`); `RECALL_1`–`RECALL_3` — по **36 связок** (ID `109`–`216`). ID строго возрастают; в актах 1–3 — **3–4** вставки `TARGET` на акт, в актах 4–6 — **5–6**. Темпы и паузы по фазам — как в «Четыре фазы».
5. **`# BRIEF:`** в шапке совпадает с сюжетом; `metadata.md` без персонажей-гидов, если в сценарии их нет.

### Отчёт пользователю

По итогам QA **обязательно** короткий отчёт **на русском**: сколько связок проверено (108 сюжет + 108 recall), что исправлено (пример: рассинхрон EN/ES в связке `060`, повтор «gentle», устаревший формат у другого эпизода).

Каркасы `skeleton_ready` (пустые `ACT_*`) сюжетом **не заполнять** в том же задании; для них достаточно проверки `# BRIEF:` и `metadata.md`.

## Слоты

В `script.md` только `NARRATOR_NATIVE_<язык>` и `NARRATOR_TARGET_<язык>`. Суффикс языка = `NATIVE_LANG` / `TARGET_LANG`.

Голос берётся из ключей конфига, не из имени слота: `NARRATOR_NATIVE_*` → `MENTOR_NATIVE`, `NARRATOR_TARGET_*` → `MENTOR_TARGET`. Слоты `MENTOR_*` в сценарий не писать.

В `ACT_1`–`ACT_6` большинство связок содержат только `NARRATOR_NATIVE_<язык>`. Целевой слот не ставится в каждую связку.

Там, где вкрапление есть, сразу после `NARRATOR_TARGET_<язык>` идёт `NARRATOR_NATIVE_<язык>_translate` с тем же ID и **тем же темпом, что `NARRATOR_NATIVE` в этом акте** (фазы 1–2: `0.85`, фазы 4–6 в сюжетных актах: `0.82`). Голос тот же `MENTOR_NATIVE`. Пример: `[005|NARRATOR_NATIVE_en_translate|0.85]` в `ACT_2`.

В хуке `000` три разных слота, чтобы кэш не затирал строки: `NARRATOR_NATIVE_en`, `NARRATOR_NATIVE_en_scene`, `NARRATOR_NATIVE_en_lantern`. Слот `_slow` в сценарий не добавлять. Один смысловой ID на связку; внутри связки каждый слот один раз. ID только возрастают.

В `RECALL` допустим второй целевой слот `NARRATOR_TARGET_<язык>_repeat`. Это медленный повтор той же фразы, голос тот же `MENTOR_TARGET`. В `mode=target_only` слоты с суффиксом `_repeat` не озвучиваются.

## Четыре фазы

Сюжет и порядок сцен берутся только из `# BRIEF:` эпизода. В `ACT_1`–`ACT_6` красота только в `NARRATOR_NATIVE`: связные предложения, свет, звук, запах, фактура места. `NARRATOR_TARGET` — A1: настоящее время, короткая мысль в актах 1–3, 1–2 предложения в актах 4–6; без придаточных, прошедшего и сослагательного. **Испанский:** без роботического «Tú» в каждой строке — см. QA. **Словарь:** все ключевые слова из `Слова:` в `# BRIEF:` должны появиться в `NARRATOR_TARGET` в сюжетных актах. Внутри реплик нет других скобок, HTML и markdown.

Оценку минут одной русской строкой `#` в начале `script.md`. Остаток до 600 минут — тихий фон на сборке, не строками сценария.

**Плавный уход в сон.** Темп голоса **снижается** от первых актов к `RECALL` и финальной фазы плейлиста — не держи «бодрый» `0.88` в закреплении.

**Три части сценария, три прохода.** В `script.md` всегда есть все три части. Сдача без любой из них не готова.

1. Проход 1: `ACT_1`–`ACT_6` — основной сюжет на родном языке, ровно 108 связок. Редкие вставки `NARRATOR_TARGET` и сразу за ними `NARRATOR_NATIVE_*_translate` остаются только здесь.
2. Проход 2: `RECALL_1`–`RECALL_3` — сопоставление тех же 108 сцен (см. ниже).
3. Проход 3: `PLOT_TARGET_1`–`PLOT_TARGET_3` — тот же основной сюжет целиком на выбранном языке, плюс `PLAYLIST_SEQUENCE`.

Не сжимай вторую и третью части, чтобы вместить их в тот же ответ, что и сюжет. Проходы 2 и 3 не придумывают новый сюжет и не заполняются шаблоном.

Пропорция **1 к 1**. В `ACT_1`–`ACT_6` ровно **18 связок на акт**, всего **108**. В `RECALL_1`, `RECALL_2`, `RECALL_3` ровно **36 связок на блок**, всего **108**: по одной связке на каждую сцену `001`–`108`, ничего не выкидывая. `RECALL_1` = сцены `001`–`036`, `RECALL_2` = `037`–`072`, `RECALL_3` = `073`–`108`. ID закрепления не повторяют ID сюжета и идут следом: сцена `N` → recall `N+108`.

**Сборка RECALL сопоставлением.** Для каждой сцены акта открыть её `NARRATOR_NATIVE` и по ней собрать пару recall. Источник сюжета — только этот абзац. Порядок предметов и действий тот же.

Исключение: короткие `NARRATOR_TARGET` внутри актов и сразу идущий за ними `NARRATOR_NATIVE_*_translate` в recall не переносятся и сюжетом не считаются. Это вкрапление изучаемого языка, которое в акте уже повторено на родном. В recall нет слота `_translate` и нет пары «испанский → родной перевод».

Запрещено: общая рамка («Tú estás aquí», «El aire es suave», «La noche es tranquila», «Tú puedes descansar»), подстановка английской фразы в испанскую строку, копирование короткой вставки `TARGET` из акта вместо пересказа сцены.

Порядок одной связки recall:
1. Взять `NARRATOR_NATIVE` сцены `N`.
2. Английская строка recall — сжатие этого абзаца, те же образы, без новых деталей.
3. `NARRATOR_TARGET` — перевод 1:1 пункта 2, значит и сцены `N`.
4. `_repeat` — дословная копия пункта 3.

На QA для каждой пары `N` / `N+108` проверить, что испанский идёт по сюжету сцены акта. Рассинхрон — переписать связку recall, не акт.

**Сюжет на выбранном языке, `PLOT_TARGET_1`–`PLOT_TARGET_3`.** Это не фильтр плейлиста и не повтор коротких вставок из актов. В скрипте лежат отдельные строки. Сцена `N` → ID `N+216` (`001`→`217` … `108`→`324`). `PLOT_TARGET_1` = сцены `001`–`036`, `PLOT_TARGET_2` = `037`–`072`, `PLOT_TARGET_3` = `073`–`108`.

В связке только `NARRATOR_TARGET` (**`0.75`**) и `[PAUSE|2.5]`. Родного текста, `_translate` и `_repeat` здесь нет. Текст — пересказ `NARRATOR_NATIVE` сцены `N` на выбранном языке, 1:1: те же предметы и действия, тот же порядок, без новых деталей. Короткая вставка `TARGET` из акта сюда не копируется.

1. **Хук `000`** (внутри `ACT_1_<SCENE>`, до первого слова изучаемого языка). Три реплики только `NARRATOR_NATIVE`, темп `0.88`. Паузы `[PAUSE|3.5]`…`[PAUSE|4.0]`. Смысл: тело отпускает день; воздух и звук места из `# BRIEF:`. **Третья реплика** (`NARRATOR_NATIVE_en_lantern`): мягко обозначь **ритм выпуска** — история идёт на родном языке, изучаемый язык появляется редкими спокойными фразами, **без** урока, задания и «нужно запомнить»; слушателю достаточно оставаться в образе.
2. **Фаза 1, `ACT_1_<SCENE>`–`ACT_3_<SCENE>`.** По **18 связок** в каждом акте, ~85% родного и ~15% изучаемого. Связки без TARGET: `NARRATOR_NATIVE` **`0.85`** → `[PAUSE|1.5]` между строками внутри связки. Целевой язык в **3–4 связках акта**. Связка **с** TARGET: `NARRATOR_NATIVE` (`0.85`) → `[PAUSE|1.5]` → `NARRATOR_TARGET` (`0.78`) → `[PAUSE|1.5]` → `_translate` (`0.85`) → **`[PAUSE|2.0]`** (конец связки).
3. **Фаза 2, `ACT_4_<SCENE>`–`ACT_6_<SCENE>`.** По **18 связок**, ~70% / ~30%. `NARRATOR_NATIVE` **`0.82`**. TARGET в **5–6 связках акта**; в такой связке `NARRATOR_TARGET` **`0.75`**, `_translate` **`0.82`**, **`[PAUSE|2.5]`** в конце связки (между внутренними строками по-прежнему `[PAUSE|1.5]` где нужно).
4. **Фаза 3, `RECALL_1`, `RECALL_2`, `RECALL_3`.** Пошаговое закрепление каждой сцены `001`–`108`. Каждая связка — новый ID `109`–`216`, строго:
   1. `NARRATOR_NATIVE` (**`0.80`**) — 2–3 предложения, 25–35 слов, **только** эта сцена.
   2. `[PAUSE|2.0]`
   3. `NARRATOR_TARGET` (**`0.75`**) — 4–5 предложений A1, **25–40 слов** (короткий EN → короткий ES, без искусственного раздувания): **перевод 1:1** сцены акта-пары по правилу «Сборка RECALL сопоставлением». Без лишнего «Tú» (см. QA).
   4. `[PAUSE|2.0]`
   5. `NARRATOR_TARGET_*_repeat` (**`0.70`**) — точный повтор пункта 3.
   6. **`[PAUSE|4.0]`**
5. **Фаза 4 — не новый текст.** `PLAYLIST_SEQUENCE` проигрывает три части по порядку, `repeat=1`:
   1. `ACT_1`–`ACT_6` (`mode=full`) — сюжет на родном языке.
   2. `RECALL_1`–`RECALL_3` (`mode=full`) — закрепление.
   3. `PLOT_TARGET_1`–`PLOT_TARGET_3` (`mode=full, speed_mult=0.82`) — тот же сюжет на выбранном языке.
   Финал не заменяется строкой `target_only` по recall: испанский сюжет уже написан в `PLOT_TARGET`. Слоты `_repeat` в `target_only` по-прежнему не озвучиваются, но финальный проход их не использует. На sleep-канале дополнительно можно `SPEECH_SPEED_BIAS: 0.95` в `channel_config.md` (~5% медленнее TTS).

## Пример разметки

`es` / `en` — пример ритма, не сюжет. `<SCENE>` замени по `# BRIEF:`. В файле по 18 связок на акт и по 36 на recall. Темпы и паузы — как в «Четыре фазы».

    # EPISODE: EP02_name
    # TARGET_LANG: es
    # NATIVE_LANG: en
    # BRIEF: … Слова: lluvia, piedra, noche.
    # Оценка хронометража: речь ~220–240 мин. Остаток до 600 мин — тихий фон.

    ## BLOCK: ACT_1_<SCENE>
    [000|NARRATOR_NATIVE_en|0.88] Let the day slip from your shoulders, and let your hands rest open. Nothing you did today needs you now. The night can be slow.
    [PAUSE|3.5]
    [000|NARRATOR_NATIVE_en_scene|0.88] The air of this place is close and mild. A small sound repeats, and the dark around you stays kind.
    [PAUSE|4.0]
    [000|NARRATOR_NATIVE_en_lantern|0.88] Most of the night will stay in English, and now and then a short Spanish phrase will pass through, easy and slow. You can let the words arrive without holding them.
    [PAUSE|3.5]
    [001|NARRATOR_NATIVE_en|0.85] The nearest surface is cool under your hand. A small sound repeats in the same place. The air does not hurry. You stay with that sound.
    [PAUSE|1.5]
    [002|NARRATOR_NATIVE_en|0.85] The same place is still around you. The light is lower than before. A quiet texture meets your hand.
    [PAUSE|1.5]
    [007|NARRATOR_NATIVE_en|0.85] Rain gathers in the seam between two stones and runs toward the gutter in a thin line.
    [PAUSE|1.5]
    [007|NARRATOR_TARGET_es|0.78] La lluvia es suave.
    [PAUSE|1.5]
    [007|NARRATOR_NATIVE_en_translate|0.85] The rain is gentle.
    [PAUSE|2.0]

    ## BLOCK: ACT_2_<SCENE>
    ## BLOCK: ACT_3_<SCENE>

    ## BLOCK: ACT_4_<SCENE>
    [055|NARRATOR_NATIVE_en|0.82] The air here is cooler than the room you left. One hand stays on the stone. The lamp behind you holds its small circle.
    [PAUSE|1.5]
    [055|NARRATOR_TARGET_es|0.75] El aire es fresco. La piedra es fría.
    [PAUSE|1.5]
    [055|NARRATOR_NATIVE_en_translate|0.82] The air is cool. The stone is cold.
    [PAUSE|2.5]

    ## BLOCK: ACT_5_<SCENE>
    ## BLOCK: ACT_6_<SCENE>

    ## BLOCK: RECALL_1
    [109|NARRATOR_NATIVE_en|0.80] Warm rain finds you on the dark stone street. Water keeps to the middle of the road. A small lamp burns in a doorway ahead.
    [PAUSE|2.0]
    [109|NARRATOR_TARGET_es|0.75] Estás bajo la lluvia cálida. La calle es oscura y de piedra. El agua corre por el centro. Hay una luz pequeña en una puerta.
    [PAUSE|2.0]
    [109|NARRATOR_TARGET_es_repeat|0.70] Estás bajo la lluvia cálida. La calle es oscura y de piedra. El agua corre por el centro. Hay una luz pequeña en una puerta.
    [PAUSE|4.0]

    ## BLOCK: RECALL_2
    ## BLOCK: RECALL_3

    ## BLOCK: PLOT_TARGET_1
    [217|NARRATOR_TARGET_es|0.75] Estás bajo la lluvia cálida en la calle oscura de piedra. El agua corre por el centro. Una luz pequeña arde en una puerta.
    [PAUSE|2.5]

    ## BLOCK: PLOT_TARGET_2
    ## BLOCK: PLOT_TARGET_3

    ## PLAYLIST_SEQUENCE
    PLAY: ACT_1_<SCENE>, ACT_2_<SCENE>, ACT_3_<SCENE> (mode=full, repeat=1)
    PLAY: ACT_4_<SCENE>, ACT_5_<SCENE>, ACT_6_<SCENE> (mode=full, repeat=1)
    PLAY: RECALL_1, RECALL_2, RECALL_3 (mode=full, repeat=1)
    PLAY: PLOT_TARGET_1, PLOT_TARGET_2, PLOT_TARGET_3 (mode=full, speed_mult=0.82, repeat=1)

## visual_prompts.md

- Одна строка `PROMPT: ` на кадр: ночной пейзаж, интерьер или деталь окружения от первого лица. Людей, лиц, имён и фигур в кадре нет.
- `BASE_STYLE_PROMPT` в строки не вставлять: `generate_images.py` добавит его сам.
- Каждая строка заканчивается на `16:9, no text`.

## Остальные файлы

- `metadata.md` — `## English Metadata` и `## Russian Metadata` по `templates/episode_metadata.md`.
- `script_ru.md` не обязателен: озвучка на этом пути делает только `voiceover.mp3`.
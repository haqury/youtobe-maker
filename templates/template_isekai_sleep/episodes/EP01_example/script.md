# EPISODE: EP01_the_gate
# TARGET_LANG: es
# NATIVE_LANG: en
# Короткий образец разметки, не полная ночь. Акты 1–2: красота в MENTOR_NATIVE, изучаемый язык — короткое A1.

## BLOCK: ACT_1_FOREST
[001|MENTOR_NATIVE_en|0.90] The night forest closes softly around us, warm and dark, with no hard sound in the leaves. You can let your shoulders drop, traveler. The path under your feet is kind.
[PAUSE|1.5]
[001|transitions_bank_describe_in_native|0.90] Looking at this peaceful night, in Spanish, I would say:
[PAUSE|1.5]
[001|MENTOR_TARGET_es|0.82] Esto es un bosque.
[PAUSE|2.0]
[002|MENTOR_NATIVE_en|0.90] A low mist sits between the trunks, and the quiet here is wide enough to rest in. The rain is only a whisper on the leaves, and nothing in this dark asks you to move faster.
[PAUSE|1.5]
[002|MENTOR_TARGET_es|0.82] Un bosque tranquilo.
[PAUSE|2.0]
[003|MENTOR_TARGET_es|0.82] Yo soy un gato.
[PAUSE|2.0]
[003|transitions_bank_apology_mentor_spoke|0.90] Oh, pardon me, I forgot you are new to this place. What I just said was:
[PAUSE|1.5]
[003|MENTOR_NATIVE_en|0.90] I am a cat.
[PAUSE|2.0]
[004|NPC_TARGET_es|0.85] Buenas noches.
[PAUSE|2.0]
[004|transitions_bank_npc_statement|0.90] Don't worry, she is friendly. What she just told us is:
[PAUSE|1.5]
[004|MENTOR_NATIVE_en|0.90] Good night.
[PAUSE|2.0]

## BLOCK: ACT_2_LANTERN
[005|MENTOR_NATIVE_en|0.90] A small lantern wakes in my hand, and its dim gold draws a circle on the moss. Inside that circle the night feels near, and the next step is easy to see.
[PAUSE|1.5]
[005|transitions_bank_describe_in_native|0.90] In this language, I would say:
[PAUSE|1.5]
[005|MENTOR_TARGET_es|0.82] Esto es una linterna.
[PAUSE|2.0]
[006|MENTOR_NATIVE_en|0.90] The flame is modest, no taller than a finger, and it trembles when the wet air moves. It is enough for one step, and the rest of the dark can stay soft.
[PAUSE|1.5]
[006|MENTOR_TARGET_es|0.82] La linterna es pequeña.
[PAUSE|2.0]
[007|MENTOR_NATIVE_en|0.90] Beyond the circle the forest stays gentle, and the dark does not press on the light. We can stand here a moment and let the night be kind.
[PAUSE|1.5]
[007|MENTOR_TARGET_es|0.82] La noche es suave.
[PAUSE|2.0]

## PLAYLIST_SEQUENCE
PLAY: ACT_1_FOREST (mode=full, repeat=1)
PLAY: ACT_2_LANTERN (mode=full, repeat=1)
PLAY: ACT_1_FOREST (mode=target_only, speed_mult=0.92, extra_pause=2.0, repeat=2)
PLAY: ACT_2_LANTERN (mode=target_only, speed_mult=0.92, extra_pause=2.0, repeat=2)
PLAY: ACT_1_FOREST (mode=shuffle_target, speed_mult=0.85, extra_pause=7.0, repeat=4)
PLAY: ACT_2_LANTERN (mode=shuffle_target, speed_mult=0.85, extra_pause=7.0, repeat=4)

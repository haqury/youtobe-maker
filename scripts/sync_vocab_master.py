"""Синхронизация мастер-словаря A1–B2 с готовыми сценариями канала.

Читает `channels/<slug>/vocab_master_a1_b2.csv`, сканирует целевые реплики
(`NARRATOR_TARGET_*`, `MENTOR_TARGET_*`, `NPC_TARGET_*`, без `_repeat`/`_slow`)
во всех `playlists/*/episodes/*/script.md` и обновляет поля:
  - Status (`covered` / `todo`, не перезаписывая ручной статус `skipped`)
  - Playlist_IDs (например `S01, S06, S07`)
  - Episode_IDs (например `S06:EP01, S07:EP01`)
  - Occurrences (количество вхождений в целевых репликах)

Запуск:
  python scripts/sync_vocab_master.py
  python scripts/sync_vocab_master.py --channel channels/LumaDreamLearnSpanish
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANNEL = ROOT / "channels" / "LumaDreamLearnSpanish"
TOKEN_RE = re.compile(r"[a-záéíóúüñ]+", re.IGNORECASE)
LINE_RE = re.compile(r"^\[(\d+)\|([^|]+)\|([0-9.]+)\]\s*(.*)$")

# Ручные исключения и неправильные формы для точного сопоставления со словарной формой
IRREGULAR_FORMS: dict[str, set[str]] = {
    "ser": {"es", "son", "soy", "eres", "somos", "sea", "sean", "era", "fue", "sido"},
    "estar": {"está", "están", "estás", "estoy", "estamos", "estando", "estado"},
    "tener": {"tiene", "tienen", "tienes", "tengo", "tenemos", "teniendo", "tenido"},
    "haber": {"hay", "ha", "han", "he", "hemos", "habido"},
    "ir": {"va", "van", "vas", "voy", "vamos", "yendo", "ido"},
    "venir": {"viene", "vienen", "vienes", "vengo", "venimos", "viniendo", "venido"},
    "hacer": {"hace", "hacen", "haces", "hago", "hacemos", "haciendo", "hecho", "hecha"},
    "poder": {"puede", "pueden", "puedes", "puedo", "podemos", "pudiendo", "podido"},
    "querer": {"quiere", "quieren", "quieres", "quiero", "queremos", "queriendo", "querido"},
    "saber": {"sabe", "saben", "sabes", "sé", "sabemos", "sabido"},
    "conocer": {"conoce", "conocen", "conoces", "conozco", "conocemos"},
    "caber": {"cabe", "caben", "quepo"},
    "ver": {"ve", "ven", "ves", "veo", "vemos", "viendo", "visto", "vista"},
    "oír": {"oye", "oyen", "oyes", "oigo", "oímos", "oyendo", "oído"},
    "dar": {"da", "dan", "das", "doy", "damos", "dando", "dado"},
    "poner": {"pone", "ponen", "pones", "pongo", "ponemos", "poniendo", "puesto", "puesta"},
    "salir": {"sale", "salen", "sales", "salgo", "salimos", "saliendo", "salido"},
    "valer": {"vale", "valen", "vales", "valgo", "valemos"},
    "traer": {"trae", "traen", "traes", "traigo", "traemos", "trayendo", "traído"},
    "caer": {"cae", "caen", "caes", "caigo", "caemos", "cayendo", "caído"},
    "decir": {"dice", "dicen", "dices", "digo", "decimos", "diciendo", "dicho", "dicha"},
    "dormir": {"duerme", "duermen", "duermes", "duermo", "dormimos", "durmiendo", "dormido"},
    "morir": {"muere", "mueren", "mueres", "muero", "morimos", "muriendo", "muerto", "muerta"},
    "sentir": {"siente", "sienten", "sientes", "siento", "sentimos", "sintiendo", "sentido"},
    "mentir": {"miente", "mienten", "mientes", "miento"},
    "preferir": {"prefiere", "prefieren", "prefieres", "prefiero"},
    "sugerir": {"sugiere", "sugieren", "sugieres", "sugiero"},
    "convertir": {"convierte", "convierten", "conviertes"},
    "hervir": {"hierve", "hierven"},
    "divertir": {"divierte", "divierten", "diviertes"},
    "advertir": {"advierte", "advierten"},
    "herir": {"hiere", "hieren"},
    "pedir": {"pide", "piden", "pides", "pido", "pidiendo"},
    "repetir": {"repite", "repiten", "repites", "repito", "repitiendo"},
    "medir": {"mide", "miden", "mides", "mido"},
    "vestir": {"viste", "visten", "vistes", "visto"},
    "servir": {"sirve", "sirven", "sirves", "sirvo"},
    "impedir": {"impide", "impiden"},
    "despedir": {"despide", "despiden", "despides"},
    "elegir": {"elige", "eligen", "eliges", "elijo"},
    "corregir": {"corrige", "corrigen"},
    "seguir": {"sigue", "siguen", "sigues", "sigo", "siguiendo"},
    "conseguir": {"consigue", "consiguen", "consigues", "consigo"},
    "sonreír": {"sonríe", "sonríen", "sonríes", "sonrío", "sonriendo"},
    "reír": {"ríe", "ríen", "ríes", "río", "riendo"},
    "volver": {"vuelve", "vuelven", "vuelves", "vuelvo", "volviendo", "vuelto", "vuelta"},
    "devolver": {"devuelve", "devuelven", "devuelves", "devuelto"},
    "resolver": {"resuelve", "resuelven", "resuelto"},
    "envolver": {"envuelve", "envuelven", "envuelves", "envolviendo", "envuelto"},
    "disolver": {"disuelve", "disuelven", "disuelves", "disolviendo", "disuelto"},
    "mover": {"mueve", "mueven", "mueves", "muevo", "moviendo"},
    "llover": {"llueve", "lloviendo", "llovido"},
    "oler": {"huele", "huelen", "hueles", "huelo"},
    "morder": {"muerde", "muerden"},
    "torcer": {"tuerce", "tuercen"},
    "cerrar": {"cierra", "cierran", "cierras", "cierro", "cerrando", "cerrado", "cerrada"},
    "empezar": {"empieza", "empiezan", "empiezas", "empiezo", "empezando"},
    "comenzar": {"comienza", "comienzan", "comienzas", "comienzo"},
    "pensar": {"piensa", "piensan", "piensas", "pienso"},
    "sentar": {"sienta", "sientan", "sientas", "siento"},
    "calentar": {"calienta", "calientan", "calientas"},
    "despertar": {"despierta", "despiertan", "despiertas"},
    "nevar": {"nieva", "nevando"},
    "regar": {"riega", "riegan"},
    "sembrar": {"siembra", "siembran"},
    "temblar": {"tiembla", "tiemblan", "tiemblas"},
    "recomendar": {"recomienda", "recomiendan"},
    "negar": {"niega", "niegan"},
    "atravesar": {"atraviesa", "atraviesan", "atraviesas"},
    "descender": {"desciende", "descienden", "desciendes", "descendiendo"},
    "ascender": {"asciende", "ascienden", "asciendes", "ascendiendo"},
    "encender": {"enciende", "encienden", "enciendes"},
    "extender": {"extiende", "extienden", "extiendes", "extendiendo"},
    "entender": {"entiende", "entienden", "entiendes"},
    "atender": {"atiende", "atienden", "atiendes"},
    "defender": {"defiende", "defienden", "defiendes"},
    "perder": {"pierde", "pierden", "pierdes", "pierdo"},
    "tender": {"tiende", "tienden", "tiendes"},
    "sostener": {"sostiene", "sostienen", "sostienes", "sosteniendo"},
    "mantener": {"mantiene", "mantienen", "mantienes"},
    "contener": {"contiene", "contienen", "contienes"},
    "detener": {"detiene", "detienen", "detienes"},
    "obtener": {"obtiene", "obtienen", "obtienes"},
    "retener": {"retiene", "retienen"},
    "recordar": {"recuerda", "recuerdan", "recuerdas", "recuerdo"},
    "soñar": {"sueña", "sueñan", "sueñas", "sueño"},
    "sonar": {"suena", "suenan", "suenas"},
    "contar": {"cuenta", "cuentan", "cuentas"},
    "mostrar": {"muestra", "muestran", "muestras"},
    "demostrar": {"demuestra", "demuestran"},
    "encontrar": {"encuentra", "encuentran", "encuentras"},
    "probar": {"prueba", "prueban", "pruebas"},
    "comprobar": {"comprueba", "comprueban"},
    "aprobar": {"aprueba", "aprueban"},
    "volar": {"vuela", "vuelan", "vuelas", "volando"},
    "colgar": {"cuelga", "cuelgan", "cuelgas"},
    "jugar": {"juega", "juegan", "juegas"},
    "acostar": {"acuesta", "acuestan", "acuestas"},
    "costar": {"cuesta", "cuestan"},
    "almorzar": {"almuerza", "almuerzan", "almuerzas"},
    "rogar": {"ruega", "ruegan"},
    "soltar": {"suelta", "sueltan", "sueltas"},
    "renovar": {"renueva", "renuevan"},
    "fluir": {"fluye", "fluyen", "fluyes", "fluyendo"},
    "construir": {"construye", "construyen", "construyendo"},
    "destruir": {"destruye", "destruyen"},
    "incluir": {"incluye", "incluyen"},
    "concluir": {"concluye", "concluyen"},
    "influir": {"influye", "influyen"},
    "huir": {"huye", "huyen"},
    "distinguir": {"distingue", "distinguen", "distingues"},
    "abrir": {"abre", "abren", "abres", "abriendo", "abierto", "abierta", "abiertos", "abiertas"},
    "cubrir": {"cubre", "cubren", "cubres", "cubriendo", "cubierto", "cubierta", "cubiertos", "cubiertas"},
    "descubrir": {"descubre", "descubren", "descubres", "descubierto", "descubierta"},
    "escribir": {"escribe", "escriben", "escribes", "escribiendo", "escrito", "escrita"},
    "romper": {"rompe", "rompen", "rompes", "rompiendo", "roto", "rota"},
    "leer": {"lee", "leen", "lees", "leyendo", "leído"},
    "creer": {"cree", "creen", "crees", "creyendo", "creído"},
    "poseer": {"posee", "poseen"},
    "proveer": {"provee", "proveen"},
    "producir": {"produce", "producen", "produzco"},
    "conducir": {"conduce", "conducen", "conduzco"},
    "traducir": {"traduce", "traducen", "traduzco"},
    "reducir": {"reduce", "reducen", "reduzco"},
    "crecer": {"crece", "crecen", "crezco"},
    "nacer": {"nace", "nacen", "nazco"},
    "ofrecer": {"ofrece", "ofrecen", "ofrezco"},
    "parecer": {"parece", "parecen", "parezco"},
    "aparecer": {"aparece", "aparecen", "aparezco"},
    "desaparecer": {"desaparece", "desaparecen"},
    "permanecer": {"permanece", "permanecen"},
    "pertenecer": {"pertenece", "pertenecen"},
    "merecer": {"merece", "merecen"},
    "reconocer": {"reconoce", "reconocen", "reconozco"},
    "obedecer": {"obedece", "obedecen"},
    "establecer": {"establece", "establecen"},
    "primero": {"primero", "primera", "primer", "primeros", "primeras"},
    "tercero": {"tercero", "tercera", "tercer", "terceros", "terceras"},
    "uno": {"uno", "una", "un", "unos", "unas"},
    "cien": {"cien", "ciento", "cientos"},
    "grande": {"grande", "grandes", "gran"},
    "bueno": {"bueno", "buena", "buen", "buenos", "buenas"},
    "malo": {"malo", "mala", "mal", "malos", "malas"},
    "alguno": {"alguno", "alguna", "algún", "algunos", "algunas"},
    "ninguno": {"ninguno", "ninguna", "ningún"},
    "cualquiera": {"cualquier", "cualquiera"},
    "luz": {"luz", "luces"},
    "vez": {"vez", "veces"},
    "voz": {"voz", "voces"},
    "raíz": {"raíz", "raíces"},
    "lápiz": {"lápiz", "lápices"},
    "nariz": {"nariz", "narices"},
    "cruz": {"cruz", "cruces"},
    "paz": {"paz", "paces"},
    "pez": {"pez", "peces"},
    "nuez": {"nuez", "nueces"},
    "feliz": {"feliz", "felices"},
    "capaz": {"capaz", "capaces"},
    "eficaz": {"eficaz", "eficaces"},
    "juez": {"juez", "jueces"},
    "rey": {"rey", "reyes"},
    "ley": {"ley", "leyes"},
    "buey": {"buey", "bueyes"},
    "joven": {"joven", "jóvenes"},
    "imagen": {"imagen", "imágenes"},
    "origen": {"origen", "orígenes"},
    "margen": {"margen", "márgenes"},
    "orden": {"orden", "órdenes"},
    "examen": {"examen", "exámenes"},
    "este": {"este", "esta", "estos", "estas", "esto"},
    "ese": {"ese", "esa", "esos", "esas", "eso"},
    "aquel": {"aquel", "aquella", "aquellos", "aquellas", "aquello"},
    "quién": {"quien", "quién", "quienes", "quiénes"},
    "cuál": {"cual", "cuál", "cuales", "cuáles"},
    "cuánto": {"cuanto", "cuánto", "cuanta", "cuánta", "cuantos", "cuántos", "cuantas", "cuántas"},
}


def strip_accents_tail(s: str) -> str:
    return (
        s.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )


def forms_for_entry(word: str, pos: str) -> set[str]:
    w = word.strip().lower()
    forms: set[str] = {w}
    if " " in w:
        return forms
    if w in IRREGULAR_FORMS:
        forms.update(IRREGULAR_FORMS[w])

    if pos == "verb" and len(w) > 3:
        if w.endswith("ar"):
            stem = w[:-2]
            forms.update({
                stem + "o", stem + "as", stem + "a", stem + "amos", stem + "an",
                stem + "ando", stem + "ado", stem + "ada", stem + "ados", stem + "adas",
                stem + "e", stem + "es", stem + "en",
            })
            if w.endswith("gar"):
                forms.add(w[:-3] + "gue")
                forms.add(w[:-3] + "guen")
            if w.endswith("car"):
                forms.add(w[:-3] + "que")
                forms.add(w[:-3] + "quen")
            if w.endswith("zar"):
                forms.add(w[:-3] + "ce")
                forms.add(w[:-3] + "cen")
        elif w.endswith("er"):
            stem = w[:-2]
            forms.update({
                stem + "o", stem + "es", stem + "e", stem + "emos", stem + "en",
                stem + "iendo", stem + "ido", stem + "ida", stem + "idos", stem + "idas",
                stem + "a", stem + "an",
            })
            if w.endswith("ger"):
                forms.add(w[:-3] + "jo")
                forms.add(w[:-3] + "ja")
        elif w.endswith("ir") or w.endswith("ír"):
            stem = w[:-2]
            forms.update({
                stem + "o", stem + "es", stem + "e", stem + "imos", stem + "en",
                stem + "iendo", stem + "ido", stem + "ida", stem + "idos", stem + "idas",
                stem + "a", stem + "an",
            })
            if w.endswith("gir"):
                forms.add(w[:-3] + "jo")
                forms.add(w[:-3] + "ja")
    elif pos in {"noun", "adj", "numeral", "pron"}:
        if w.endswith("o"):
            base = w[:-1]
            forms.update({base + "o", base + "a", base + "os", base + "as"})
        elif w.endswith(("a", "e", "é", "á", "ó")):
            forms.add(w + "s")
        elif w.endswith(("í", "ú")):
            forms.update({w + "s", w + "es"})
        elif w.endswith(("r", "l", "n", "d", "j", "y", "s")):
            forms.add(w + "es")
            if w.endswith(("ón", "án", "én", "ín", "és")):
                forms.add(strip_accents_tail(w) + "es")
        elif w.endswith("z"):
            forms.add(w[:-1] + "ces")
        if w.endswith("dor"):
            forms.update({w + "a", w + "es", w + "as"})
        if w.endswith("és"):
            base = strip_accents_tail(w[:-1]) + "s"
            forms.update({base + "a", base + "es", base + "as"})
    return forms


def is_target_line(slot: str) -> bool:
    low = slot.lower()
    if "_repeat" in low or "_slow" in low:
        return False
    return "_target_" in low or low in {"mentor_target", "npc_target"}


def scan_channel(channel_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Возвращает token -> список (playlist_id, episode_key) по всем вхождениям."""
    hits: dict[str, list[tuple[str, str]]] = defaultdict(list)
    playlists_dir = channel_dir / "playlists"
    if not playlists_dir.is_dir():
        return hits

    for script_path in sorted(playlists_dir.glob("*/episodes/*/script.md")):
        playlist_folder = script_path.parents[2].name
        episode_folder = script_path.parent.name
        playlist_id = playlist_folder.split("_", 1)[0]
        ep_id = episode_folder.split("_", 1)[0]
        ep_key = f"{playlist_id}:{ep_id}"

        text = script_path.read_text(encoding="utf-8-sig")
        for raw_line in text.splitlines():
            m = LINE_RE.match(raw_line.strip())
            if not m:
                continue
            _sid, slot, _spd, body = m.groups()
            if not is_target_line(slot):
                continue
            low_body = body.lower()
            for tok in TOKEN_RE.findall(low_body):
                hits[tok].append((playlist_id, ep_key))
            hits[f"__phrase__:{low_body}"].append((playlist_id, ep_key))
    return hits


def sync_master(channel_dir: Path, dry_run: bool = False) -> int:
    csv_path = channel_dir / "vocab_master_a1_b2.csv"
    if not csv_path.is_file():
        print(f"Не найден файл: {csv_path}", file=sys.stderr)
        return 1

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    hits = scan_channel(channel_dir)
    phrase_entries = [
        (k[len("__phrase__:"):], v)
        for k, v in hits.items()
        if k.startswith("__phrase__:")
    ]

    covered_by_cefr: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        word = row["Word_ES"].strip()
        pos = row["POS"].strip().lower()
        cefr = row["CEFR"].strip().upper()
        manual_status = row["Status"].strip().lower()

        matched_occ: list[tuple[str, str]] = []
        if " " in word:
            pat = re.compile(rf"\b{re.escape(word.lower())}\b")
            for p_text, occs in phrase_entries:
                if pat.search(p_text):
                    matched_occ.extend(occs)
        else:
            for form in forms_for_entry(word, pos):
                matched_occ.extend(hits.get(form, []))

        if matched_occ:
            pl_ids = sorted({p for p, _ in matched_occ})
            ep_ids = sorted({e for _, e in matched_occ})
            if manual_status != "skipped":
                row["Status"] = "covered"
            row["Playlist_IDs"] = "; ".join(pl_ids)
            row["Episode_IDs"] = "; ".join(ep_ids)
            row["Occurrences"] = str(len(matched_occ))
        else:
            if manual_status != "skipped":
                row["Status"] = "todo"
            row["Playlist_IDs"] = ""
            row["Episode_IDs"] = ""
            row["Occurrences"] = "0"

        covered_by_cefr[cefr][1] += 1
        if row["Status"] == "covered":
            covered_by_cefr[cefr][0] += 1

    if not dry_run:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    total_cov = sum(v[0] for v in covered_by_cefr.values())
    total_all = sum(v[1] for v in covered_by_cefr.values())
    print(f"Всего слов в мастер-листе: {total_all} | Закрыто: {total_cov} ({total_cov * 100 // max(total_all, 1)}%)")
    for lvl in ("A1", "A2", "B1", "B2"):
        c, t = covered_by_cefr.get(lvl, [0, 0])
        print(f"  {lvl}: {c}/{t}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синхронизация vocab_master_a1_b2.csv со сценариями.")
    parser.add_argument("--channel", type=Path, default=DEFAULT_CHANNEL, help="Папка канала")
    parser.add_argument("--dry-run", action="store_true", help="Только показать статистику без записи")
    args = parser.parse_args(argv)
    return sync_master(args.channel.resolve(), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

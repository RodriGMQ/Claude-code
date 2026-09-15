#!/usr/bin/env python3
"""
clipper.py — de un video largo a clips verticales para TikTok.

Flujo:
  1. extrae el audio y lo transcribe con Whisper (faster-whisper) con marcas por palabra
  2. arma frases a partir de la puntuacion y de las pausas reales del hablante
  3. puntua ventanas de frases y elige los mejores cortes, sin solaparse
  4. renderiza cada corte en 1080x1920 con subtitulo quemado, palabra por palabra

La transcripcion se cachea junto al video (<video>.transcript.json), asi que
volver a renderizar con otros parametros no vuelve a pasar Whisper.

Uso tipico:
    python3 clipper.py entrevista.mp4 -n 3
    python3 clipper.py entrevista.mp4 --layout crop --case normal --accent 00E5FF
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------- utilidades

def run(cmd, **kw):
    """Ejecuta un comando y devuelve stdout; aborta con mensaje legible si falla."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)
    if p.returncode != 0:
        sys.exit("\n[error] fallo el comando:\n  %s\n%s" % (" ".join(cmd), p.stderr.strip()[-2000:]))
    return p.stdout


def need(binary):
    if shutil.which(binary) is None:
        sys.exit("[error] falta '%s' en el PATH. Instalalo antes de seguir "
                 "(ver clips/README.md)." % binary)


def probe(video):
    """Datos basicos del archivo de entrada."""
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", video])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        sys.exit("[error] el archivo no tiene pista de video: %s" % video)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": round(fps, 3) or 30.0,
        "duration": float(data["format"]["duration"]),
        "has_audio": a is not None,
    }

# ------------------------------------------------------------ transcripcion

def extract_audio(video, wav):
    run(["ffmpeg", "-y", "-v", "error", "-i", video,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])


def transcribe(video, model_name, lang, cache_path, compute_type):
    """Devuelve [{w, start, end}] con una entrada por palabra."""
    if os.path.exists(cache_path):
        print("[1/4] transcripcion cacheada: %s" % cache_path)
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)["words"]

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("[error] falta faster-whisper. Instalalo con:\n"
                 "    pip install -r clips/requirements.txt\n"
                 "O pasa una transcripcion ya hecha en %s" % cache_path)

    print("[1/4] transcribiendo con Whisper (%s)... la primera vez descarga el modelo"
          % model_name)
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio.wav")
        extract_audio(video, wav)
        model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
        segments, info = model.transcribe(
            wav,
            language=lang,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            beam_size=5,
        )
        words = []
        for seg in segments:
            for w in (seg.words or []):
                text = w.word.strip()
                if text:
                    words.append({"w": text, "start": round(w.start, 3), "end": round(w.end, 3)})
        detected = getattr(info, "language", lang)

    if not words:
        sys.exit("[error] Whisper no encontro voz en el audio. Revisa que el video tenga "
                 "habla audible, o pasa --lang explicito.")

    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"language": detected, "words": words}, fh, ensure_ascii=False, indent=1)
    print("      %d palabras, idioma detectado: %s" % (len(words), detected))
    return words

# ------------------------------------------------------- frases y seleccion

TERMINAL = tuple(".?!…")
SOFT = tuple(",;:")


@dataclass
class Sentence:
    start: float
    end: float
    words: list = field(default_factory=list)

    @property
    def text(self):
        return " ".join(w["w"] for w in self.words)

    @property
    def closed(self):
        return self.text.rstrip().endswith(TERMINAL)


def build_sentences(words, max_len=13.0, pause=0.65):
    """Agrupa palabras en frases usando puntuacion y silencios reales."""
    sentences, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99.0
        long_enough = (w["end"] - cur[0]["start"]) >= max_len
        if w["w"].rstrip().endswith(TERMINAL) or gap >= pause or long_enough or nxt is None:
            sentences.append(Sentence(cur[0]["start"], cur[-1]["end"], cur))
            cur = []
    return sentences


HOOKS = [
    r"^(por que|porque|como|cuando|donde|quien|cuanto|que pasa|sabias|te (voy|has|hab))",
    r"\b(el error|los errores|el problema|el secreto|la clave|lo que nadie|nadie te dice)\b",
    r"\b(nunca|jamas|siempre|ojo|cuidado|atencion|escucha|mira|imagina)\b",
    r"\b(primero|segundo|tercero|paso 1|tres|dos|cinco) (cosas|claves|razones|errores|pasos|tips)\b",
    r"\b(la verdad|en realidad|resulta que|lo mas importante|te explico|te cuento)\b",
    r"^(why|how|what|when|the (mistake|problem|secret|key)|nobody|never|listen|look)",
]
NUMERIC = re.compile(r"\d|\b(por ciento|porciento|soles|dolares|minutos|horas|dias|veces)\b")

# Saludos y despedidas: son el peor arranque posible para un clip suelto.
FILLER = [
    r"^(hola|buenas|buenos dias|buenas tardes|buenas noches|que tal|hey|bienvenid)",
    r"\b(bienvenidos al canal|mi nombre es|soy .{0,25} y (hoy|en este)|en el video de hoy)\b",
    r"\b(suscrib|dale like|comenta abajo|activa la campanita|nos vemos|hasta la proxima)\b",
    r"\b(si te (sirvio|gusto)|comparte(lo)?|guarda el video|no olvides)\b",
    r"^(entonces|bueno|este|o sea|a ver|entonces bueno)\b.{0,12}$",
]


def _norm(s):
    """Minusculas sin tildes, para que los patrones no dependan de la acentuacion."""
    s = s.lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        s = s.replace(a, b)
    return s


def score_window(sents, i, j, target, prev_gap):
    """Puntua la ventana sents[i:j]. Mas alto = mejor clip."""
    window = sents[i:j]
    dur = window[-1].end - window[0].start
    head = _norm(window[0].text)
    score = 0.0

    for pat in HOOKS:                                    # arranque con gancho
        if re.search(pat, head):
            score += 3.0
            break
    if head.startswith(("por que", "como ", "que ", "cuanto", "sabias")) or "?" in window[0].text:
        score += 1.5
    if NUMERIC.search(head):                             # cifras concretas enganchan
        score += 1.0
    if window[-1].closed:                                # cierra en punto, no a media idea
        score += 2.0
    if prev_gap >= 0.5:                                  # entra despues de un silencio
        score += 1.2
    for pat in FILLER:                                   # saludo, muletilla o despedida
        if re.search(pat, head):
            score -= 4.0
            break
    if i == 0:                                           # el arranque casi nunca es el mejor gancho
        score -= 1.5
    if j == len(sents):                                  # el cierre suele ser la despedida
        score -= 1.0

    nwords = sum(len(s.words) for s in window)
    wps = nwords / dur if dur else 0
    if 2.0 <= wps <= 4.2:                                # ritmo natural, ni pausado ni atropellado
        score += 1.0
    elif wps < 1.2:
        score -= 1.5

    score -= abs(dur - target) / target * 2.5            # cerca de la duracion objetivo
    return score


def fallback_split(sents, n, dmin, dmax):
    """Ultimo recurso: reparte el material en n bloques pegados a limites de frase."""
    if not sents or n <= 0:
        return []
    total_start, total_end = sents[0].start, sents[-1].end
    step = (total_end - total_start) / n
    out, used = [], 0
    for k in range(n):
        want_s = total_start + k * step
        want_e = min(want_s + min(max(step, dmin), dmax), total_end)
        block = [s for s in sents[used:] if s.start >= want_s - 0.01 and s.end <= want_e + 2.0]
        if not block:
            block = sents[used:used + 1]
        if not block:
            break
        used = sents.index(block[-1]) + 1
        out.append((0.0, block[0].start, block[-1].end))
    return out


def _candidates(sents, dmin, dmax, target):
    """Todas las ventanas de frases completas que duran entre dmin y dmax, puntuadas."""
    out = []
    for i in range(len(sents)):
        prev_gap = sents[i].start - sents[i - 1].end if i else 99.0
        for j in range(i + 1, len(sents) + 1):
            dur = sents[j - 1].end - sents[i].start
            if dur < dmin:
                continue
            if dur > dmax:
                break
            out.append((score_window(sents, i, j, target, prev_gap), sents[i].start,
                        sents[j - 1].end))
    out.sort(key=lambda c: -c[0])
    return out


def _fits(cand, picked, sep=0.7):
    _, s, e = cand
    return all(e <= ps - sep or s >= pe + sep for _, ps, pe in picked)


def select_clips(sents, n, dmin, dmax, target):
    """Elige hasta n ventanas no solapadas, de mejor a peor.

    Si el material no da para n clips con la duracion minima pedida, no tira a la
    basura los cortes buenos ya encontrados: relaja el minimo solo para llenar los
    huecos que quedaron libres.
    """
    picked = []
    for cand in _candidates(sents, dmin, dmax, target):
        if _fits(cand, picked):
            picked.append(cand)
            if len(picked) >= n:
                return sorted(picked, key=lambda p: p[1])

    floor = max(8.0, dmin * 0.55)
    relaxed = dmin
    while len(picked) < n and relaxed > floor:
        relaxed = max(floor, relaxed * 0.75)
        for cand in _candidates(sents, relaxed, dmax, target):
            if _fits(cand, picked):
                picked.append(cand)
                if len(picked) >= n:
                    break

    if len(picked) < n and sents:                 # sin frases utiles: reparto parejo
        picked += [c for c in fallback_split(sents, n - len(picked), dmin, dmax)
                   if _fits(c, picked)]
    return sorted(picked, key=lambda p: p[1])

# ----------------------------------------------------------------- subtitulo

def ass_color(hexrgb, alpha="00"):
    """#RRGGBB -> &HAABBGGRR& que es lo que entiende ASS."""
    h = hexrgb.lstrip("#")
    return "&H%s%s%s%s&" % (alpha, h[4:6], h[2:4], h[0:2])


def ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%05.2f" % (int(h), int(m), s)


def pick_font(requested):
    """Helvetica de verdad si existe; si no, el clon metrico disponible."""
    if requested:
        return requested
    try:
        installed = run(["fc-list", "--format", "%{family}\n"]).lower()
    except SystemExit:
        installed = ""
    for name in ("Helvetica Neue", "Helvetica", "Arial", "Liberation Sans", "Arimo"):
        if name.lower() in installed:
            return name
    return "Liberation Sans"


def chunk_words(words, per_line, max_span):
    """Parte el texto en grupos cortos: 2-3 palabras grandes leen mejor en vertical."""
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        span = cur[-1]["end"] - cur[0]["start"]
        ends_phrase = w["w"].rstrip().endswith(TERMINAL + SOFT)
        if len(cur) >= per_line or span >= max_span or ends_phrase:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def clean_word(raw, upper):
    w = raw.strip().rstrip(",.;:")
    w = w.replace("{", "(").replace("}", ")")     # las llaves son sintaxis ASS
    return w.upper() if upper else w


def write_ass(path, words, t0, font, size, accent, upper, per_line, margin_v):
    """Subtitulo quemado, una palabra resaltada a la vez (estilo TikTok)."""
    white = ass_color("FFFFFF")
    acc = ass_color(accent)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{size},{white},{white},&H00101010&,&H90000000&,-1,0,0,0,100,100,1,0,1,6,2,2,90,90,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    chunks = chunk_words(words, per_line, 1.9)
    for ci, chunk in enumerate(chunks):
        nxt_chunk_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        for wi, w in enumerate(chunk):
            start = w["start"] - t0
            if wi + 1 < len(chunk):
                end = min(chunk[wi + 1]["start"], w["end"] + 0.45) - t0
            else:
                end = w["end"] + 0.30
                if nxt_chunk_start is not None:
                    end = min(end, nxt_chunk_start)
                end -= t0
            if end <= start:
                end = start + 0.08

            parts = []
            for k, ww in enumerate(chunk):
                txt = clean_word(ww["w"], upper)
                if k == wi:
                    # resaltado + un pop corto que da sensacion de ritmo
                    parts.append(r"{\c%s\fscx112\fscy112\t(0,110,\fscx100\fscy100)}%s{\c%s}"
                                 % (acc, txt, white))
                else:
                    parts.append(txt)
            lines.append("Dialogue: 0,%s,%s,Sub,,0,0,0,,%s" %
                         (ass_time(start), ass_time(end), " ".join(parts)))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n".join(lines) + "\n")


def write_srt(path, words, t0, per_line=7):
    """SRT normal, por si quieres reeditar en CapCut o Premiere."""
    def fmt(t):
        t = max(0.0, t)
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return "%02d:%02d:%02d,%03d" % (h, m, s, ms)

    blocks = []
    for n, i in enumerate(range(0, len(words), per_line), 1):
        group = words[i:i + per_line]
        blocks.append("%d\n%s --> %s\n%s\n" % (
            n, fmt(group[0]["start"] - t0), fmt(group[-1]["end"] - t0),
            " ".join(w["w"].strip() for w in group)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(blocks))

# ------------------------------------------------------------------ render

def build_filter(layout, info, fade, dur, ass_name):
    """Arma el grafo de filtros: encuadre 9:16 + subtitulo + fundidos."""
    vertical_src = info["height"] >= info["width"]
    if layout == "auto":
        layout = "fit" if vertical_src else "blur"

    if layout == "crop":
        chain = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                 "crop=1080:1920,setsar=1[v0];")
    elif layout == "fit":
        chain = ("[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                 "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v0];")
    else:  # blur: el video completo sobre su propio fondo desenfocado, sin recortar caras
        chain = ("[0:v]split=2[bgsrc][fgsrc];"
                 "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,"
                 "crop=1080:1920,gblur=sigma=34,eq=brightness=-0.14:saturation=1.1[bg];"
                 "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                 "[bg][fg]overlay=(W-w)/2:(H-h)*0.40,setsar=1[v0];")

    chain += "[v0]ass=%s[v1];" % ass_name
    chain += ("[v1]fade=t=in:st=0:d=%.2f,fade=t=out:st=%.2f:d=%.2f,format=yuv420p[v]"
              % (fade, max(0.0, dur - fade), fade))
    return chain


def render_clip(video, start, dur, ass_path, out_path, info, fade, fps, crf):
    workdir = os.path.dirname(ass_path) or "."
    vf = build_filter(ARGS.layout, info, fade, dur, os.path.basename(ass_path))
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-stats",
        "-ss", "%.3f" % start, "-i", os.path.abspath(video), "-t", "%.3f" % dur,
        "-filter_complex", vf, "-map", "[v]",
    ]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0",
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d=%.2f,"
                       "afade=t=out:st=%.2f:d=%.2f" % (fade, max(0.0, dur - fade), fade),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-movflags", "+faststart", os.path.abspath(out_path)]
    run(cmd, cwd=workdir)

# -------------------------------------------------------------------- main

def main():
    global ARGS
    ap = argparse.ArgumentParser(
        description="Corta un video largo en clips verticales con subtitulo quemado.")
    ap.add_argument("video", help="archivo de entrada (mp4, mov, mkv...)")
    ap.add_argument("-n", "--clips", type=int, default=2, help="cuantos clips sacar (minimo 2)")
    ap.add_argument("-o", "--outdir", default=None, help="carpeta de salida (por defecto <video>_clips)")
    ap.add_argument("--min", type=float, default=18.0, help="duracion minima por clip, en segundos")
    ap.add_argument("--max", type=float, default=58.0, help="duracion maxima por clip, en segundos")
    ap.add_argument("--target", type=float, default=32.0, help="duracion ideal por clip")
    ap.add_argument("--lang", default="es", help="idioma del audio ('auto' para detectar)")
    ap.add_argument("--model", default="small",
                    help="modelo Whisper: tiny, base, small, medium, large-v3")
    ap.add_argument("--compute-type", default="int8", help="int8 (CPU) o float16 (GPU)")
    ap.add_argument("--layout", default="auto", choices=["auto", "blur", "crop", "fit"],
                    help="encuadre 9:16: blur=fondo desenfocado, crop=recorte central")
    ap.add_argument("--font", default=None, help="tipografia del subtitulo (default: Helvetica si existe)")
    ap.add_argument("--font-size", type=int, default=92)
    ap.add_argument("--accent", default="FFD400", help="color del resaltado, hex RRGGBB")
    ap.add_argument("--case", default="upper", choices=["upper", "normal"])
    ap.add_argument("--words-per-line", type=int, default=3)
    ap.add_argument("--margin-v", type=int, default=430,
                    help="altura del subtitulo desde abajo (deja libre la UI de TikTok)")
    ap.add_argument("--fade", type=float, default=0.25)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--transcript", default=None, help="json de transcripcion ya hecho")
    ap.add_argument("--no-render", action="store_true", help="solo elegir cortes y escribir el plan")
    ARGS = ap.parse_args()

    need("ffmpeg"); need("ffprobe")
    if not os.path.exists(ARGS.video):
        sys.exit("[error] no existe el archivo: %s" % ARGS.video)
    n = max(2, ARGS.clips)

    info = probe(ARGS.video)
    print("[0/4] fuente: %dx%d, %.1f s, %s audio"
          % (info["width"], info["height"], info["duration"],
             "con" if info["has_audio"] else "SIN"))
    if not info["has_audio"]:
        sys.exit("[error] el video no tiene audio, no hay nada que transcribir.")

    base = os.path.splitext(os.path.basename(ARGS.video))[0]
    outdir = ARGS.outdir or os.path.join(os.path.dirname(os.path.abspath(ARGS.video)),
                                         base + "_clips")
    os.makedirs(outdir, exist_ok=True)
    cache = ARGS.transcript or os.path.splitext(ARGS.video)[0] + ".transcript.json"

    lang = None if ARGS.lang == "auto" else ARGS.lang
    words = transcribe(ARGS.video, ARGS.model, lang, cache, ARGS.compute_type)

    sents = build_sentences(words)
    print("[2/4] %d frases detectadas" % len(sents))
    picks = select_clips(sents, n, ARGS.min, ARGS.max, ARGS.target)
    if not picks:
        sys.exit("[error] no se pudo armar ningun clip; prueba con --min mas bajo.")
    print("[3/4] %d cortes elegidos" % len(picks))
    if len(picks) < n:
        print("      aviso: pediste %d clips pero el material solo da para %d. "
              "Baja --min si quieres forzar mas." % (n, len(picks)))

    font = pick_font(ARGS.font)
    print("      tipografia del subtitulo: %s" % font)

    manifest, tmpdir = [], tempfile.mkdtemp(prefix="clipper_")
    try:
        for idx, (score, start, end) in enumerate(picks, 1):
            start = max(0.0, start - 0.25)               # un respiro antes de la primera palabra
            end = min(info["duration"], end + 0.35)
            dur = end - start
            inside = [w for w in words if w["end"] > start and w["start"] < end]
            name = "%s_clip%02d" % (base, idx)
            mp4 = os.path.join(outdir, name + ".mp4")
            srt = os.path.join(outdir, name + ".srt")
            ass = os.path.join(tmpdir, name + ".ass")

            write_ass(ass, inside, start, font, ARGS.font_size, ARGS.accent,
                      ARGS.case == "upper", ARGS.words_per_line, ARGS.margin_v)
            write_srt(srt, inside, start)
            text = " ".join(w["w"].strip() for w in inside)
            manifest.append({"file": os.path.basename(mp4), "start": round(start, 2),
                             "end": round(end, 2), "duracion": round(dur, 2),
                             "score": round(score, 2),
                             "gancho": " ".join(text.split()[:14]), "texto": text})
            print("  - clip %02d  %6.2fs -> %6.2fs  (%.1fs)  %s"
                  % (idx, start, end, dur, manifest[-1]["gancho"][:60]))
            if not ARGS.no_render:
                render_clip(ARGS.video, start, dur, ass, mp4, info,
                            ARGS.fade, ARGS.fps, ARGS.crf)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    with open(os.path.join(outdir, "clips.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print("[4/4] listo -> %s" % outdir)


if __name__ == "__main__":
    ARGS = None
    main()

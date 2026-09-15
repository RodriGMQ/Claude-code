# clipper — de un video largo a clips verticales para TikTok

Transcribe el video con Whisper, elige solos los mejores tramos y los exporta en
1080×1920 con el subtítulo quemado palabra por palabra, en tipografía Helvetica.

Se ejecuta **en tu máquina**, donde está el video y donde Whisper puede descargar su
modelo.

## Instalación (una sola vez)

**ffmpeg** — es el motor de corte y render, no se instala con pip:

| Sistema | Comando |
|---|---|
| Windows | `winget install Gyan.FFmpeg` |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |

**Whisper**:

```bash
pip install -r clips/requirements.txt
```

Comprueba que quedó bien con `ffmpeg -version` y `ffprobe -version`.

## Uso

```bash
python3 clips/clipper.py mi_video.mp4 -n 2
```

Deja junto al video una carpeta `mi_video_clips/` con:

| Archivo | Qué es |
|---|---|
| `mi_video_clip01.mp4` | el clip listo para subir, 1080×1920, subtítulo quemado |
| `mi_video_clip01.srt` | los mismos subtítulos sueltos, por si reeditas en CapCut |
| `clips.json` | corte elegido, duración, puntaje y el texto completo de cada clip |

La transcripción se guarda en `mi_video.transcript.json`. Volver a correr el comando
con otros parámetros reusa ese archivo y no vuelve a pasar Whisper, que es la parte
lenta.

## Opciones que vas a querer tocar

| Opción | Para qué |
|---|---|
| `-n 3` | cuántos clips sacar (el mínimo siempre es 2) |
| `--model medium` | más precisión en la transcripción; `small` es el default, `tiny` es el más rápido |
| `--min 20 --max 45` | rango de duración por clip, en segundos |
| `--layout crop` | recorte central en vez del fondo desenfocado |
| `--font "Helvetica Neue"` | forzar una tipografía concreta del subtítulo |
| `--font-size 82` | bajarlo si tus frases son largas y se parten en dos líneas |
| `--accent 00E5FF` | color del resaltado de la palabra activa (hex RRGGBB) |
| `--case normal` | subtítulo en minúsculas en vez de MAYÚSCULAS |
| `--margin-v 500` | subir el subtítulo si la UI de TikTok te lo tapa |
| `--no-render` | solo muestra qué cortes elegiría, sin gastar tiempo renderizando |

## Cómo elige los cortes

1. Agrupa las palabras en frases usando la puntuación **y los silencios reales** del
   hablante, así ningún clip empieza o termina a media idea.
2. Puntúa cada ventana de frases: suma por gancho de arranque (`por qué`, `el error`,
   `nadie te dice`, cifras concretas), por cerrar en punto, por entrar después de una
   pausa y por durar cerca del objetivo; **resta** por saludos, despedidas y muletillas
   (`hola a todos`, `suscríbete`, `si te sirvió`).
3. Toma los mejores sin solaparse. Si el material no da para los `n` pedidos con la
   duración mínima, la relaja solo en los huecos libres en vez de descartar los cortes
   buenos ya encontrados.

El puntaje queda en `clips.json`: uno negativo significa que ese tramo entró a la
fuerza porque pediste más clips de los que el video aguanta.

## Subtítulo

Grupos de 3 palabras, la que se está diciendo en ese momento resaltada en amarillo con
un pop corto. Tipografía: `Helvetica` si existe en el sistema, si no `Arial`, si no
`Liberation Sans` (clon métrico de Helvetica, es lo que hay en Linux). Va a 430 px del
borde inferior para no quedar debajo de la descripción y los botones de TikTok.

## Límites conocidos

- El encuadre no sigue caras: `--layout blur` (default en material horizontal) muestra
  el cuadro completo sobre su propio fondo desenfocado, y `crop` recorta el centro fijo.
  Si el hablante está muy a un lado, usa `blur`.
- La puntuación de ganchos está escrita para español; en inglés funciona pero más flojo.
- Whisper en CPU tarda: con el modelo `small`, aproximadamente la mitad de la duración
  del video. El render va aparte, alrededor de 1× tiempo real por clip.

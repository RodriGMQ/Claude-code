---
name: clips-tiktok
description: Saca clips verticales para TikTok de un video largo, con Whisper, subtítulo quemado y cortes pegados a frase completa. Úsala cuando el usuario pase un video o una ruta de video y pida clips, cortes, shorts, reels, subtítulos quemados o "sácame lo mejor de este video".
---

# Clips verticales para TikTok

Herramienta: `clips/clipper.py` en este repositorio. Transcribe con Whisper, elige los
tramos con mejor gancho y renderiza 1080×1920 con subtítulo palabra por palabra.

## Antes de correr nada

1. `ffmpeg -version` y `ffprobe -version`. Si faltan, dile al usuario cómo instalarlos
   según su sistema (`winget install Gyan.FFmpeg`, `brew install ffmpeg`,
   `sudo apt install ffmpeg`) — no intentes seguir sin ellos.
2. `python3 -c "import faster_whisper"`. Si falla: `pip install -r clips/requirements.txt`.
3. Confirma la ruta real del video con `ls`. No asumas nombre ni carpeta.

Si estás en un entorno remoto sin acceso a Google Drive ni a huggingface.co, no puedes
descargar el video ni el modelo de Whisper: dilo claro y pásale al usuario el comando
para que lo corra en su máquina, en vez de fingir que lo procesaste.

## Ejecución

```bash
python3 clips/clipper.py "<ruta del video>" -n 2
```

`-n` es cuántos clips; el mínimo es 2 aunque pidan menos. Manda siempre primero
`--no-render` para enseñar los cortes elegidos y confirmar antes de gastar el render,
salvo que el usuario pida ir directo.

La transcripción queda cacheada en `<video>.transcript.json`. Si el usuario pide
cambiar color, tipografía o duración, vuelve a correr el comando: reusa el caché y solo
renderiza de nuevo.

## Cuando el usuario pide ajustes

| Pide | Opción |
|---|---|
| "más clips" | `-n 4` |
| "más cortos" / "más largos" | `--min` y `--max` |
| "que se vea más grande el video" | `--layout crop` |
| "otro color de letra" | `--accent 00E5FF` |
| "en minúsculas" | `--case normal` |
| "el subtítulo tapa los botones" | `--margin-v 520` |
| "transcribe mejor" | `--model medium` (más lento) |

## Al terminar

Lee `clips.json` y reporta, por clip: duración, el gancho con el que arranca y el
puntaje. Un puntaje negativo significa que ese tramo entró a la fuerza porque se
pidieron más clips de los que el video aguanta — avísalo en vez de entregarlo como si
fuera bueno.

Detalle completo de la herramienta y de cómo puntúa los cortes: `clips/README.md`.

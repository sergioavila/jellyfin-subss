# Jellyfin Subss

Lee tu biblioteca de Jellyfin y busca subtítulos en los sitios de Bazarr que sirven inglés o español. No usa el plugin de OpenSubtitles de Jellyfin: Jellyfin solo entrega la lista de películas y recibe el archivo ya bajado.

## Instalar desde el App Store de CasaOS (0.4.4+)

Cuando el repo sea público y la imagen de Docker esté lista:

1. App Store → **Add Source**
2. Pega:

```
https://github.com/sergioavila/jellyfin-subss/archive/refs/heads/main.zip
```

3. Instala **Jellyfin Subss** (categoría Media)
4. En volúmenes, `/media` debe apuntar a tu librería (`/DATA/Media`)
5. Abre el panel → Ajustes → URL de Jellyfin (IP del NAS, no localhost) + API key → idiomas → proveedores

Si haces fork, cambia `sergioavila` por tu usuario de GitHub en el ZIP, en `Apps/Subss/docker-compose.yml` (`image:` e `icon:`) y vuelve a publicar.

## Publicar (una vez)

```bash
gh auth login
cd jellyfin-subss
# edita ghcr.io/sergioavila/... si tu usuario no es sergioavila
git init
git add .
git commit -m "CasaOS app store for Subss"
gh repo create jellyfin-subss --public --source=. --remote=origin --push
```

GitHub Actions publica `ghcr.io/sergioavila/jellyfin-subss:latest`. En CasaOS el campo Docker Image va **sin tag** (`ghcr.io/sergioavila/jellyfin-subss`) y el tag se elige aparte (`latest`). No uses `checho`: esa cuenta no tiene la imagen.

La imagen en GHCR es privada por defecto en cuentas nuevas: en el repo → Packages → jellyfin-subss → Package settings → Change visibility → Public.

## Qué hace falta

1. En Jellyfin, una **API key** (Dashboard → API Keys).
2. En Ajustes, los proveedores que quieras. Gestdown, Podnapisi, Subtis, YIFY, TVSubtitles, Subtitulamos, subf2m y Subtitle Cat no piden cuenta.
3. Si usas OpenSubtitles.com: cuenta + **API key de app** en [consumers](https://www.opensubtitles.com/en/consumers) (usuario, no el email). SubDL, SubSource y SubX piden su propia key.
4. Para **Traducir** (biblioteca → de → a): API key de [Gemini](https://aistudio.google.com/apikey). Opcional: API key de [TMDB](https://www.themoviedb.org/settings/api) para sinopsis y nombres de personaje.

Abre `http://IP:8787`. La URL de Jellyfin desde el contenedor suele ser `http://IP-del-host:8096`, no `localhost`.

## Qué “probar” significa

| Camino | Qué pasa |
|---|---|
| Hash match | El archivo de video es el mismo que usó quien subió el subtítulo. Se instala solo. |
| ffsubsync | Si no hay hash, alinea las marcas de tiempo con el audio. |
| Traducir | Usa un subtítulo de texto (sidecar, pista SRT/ASS del MKV vía mkvextract, o stream Jellyfin) y Gemini genera el destino; si el origen es solo imagen (DVDSUB/PGS), baja un `.srt` de un proveedor. Queda en **Revisar**. |
| Revisar | Los que no fueron hash (y las traducciones) quedan en **Revisar**. |

## Desarrollo local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8787
python -m unittest tests.test_logic
```

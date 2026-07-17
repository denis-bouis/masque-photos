#!/usr/bin/env python3
"""
Masque Photos — compose un habillage type Garmin (titre, logo/événement,
statistiques) par-dessus une ou plusieurs photos.

Prérequis : pip install Pillow fontTools

Usage (une photo ou un dossier de photos ; le même habillage est appliqué à
chacune) — génère systématiquement une version stats-à-gauche et une version
stats-à-droite par photo, dans --out-dir :
    python masque_photos.py \\
        --in-dir chemin/photo.jpg \\
        --out-dir chemin/sortie/ \\
        --event-name "TRAIL DES ALPES" \\
        --titre "L'Argentière-la-Bessée Randonnée" \\
        --date-heure "2 mai 2026 09:07" \\
        --lieu "L'Argentière-la-Bessée" \\
        --stat "DISTANCE|27,13|km" \\
        --stat "TEMPS TOTAL|6:55:07|" \\
        --stat "DÉNIVELÉ +|1 240|m"
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

FONT_BOLD = "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"
FONT_REGULAR = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"


def _font_cmap(path: str) -> set[int]:
    try:
        from fontTools.ttLib import TTFont
        return set(TTFont(path).getBestCmap().keys())
    except Exception:
        return set()


_CMAPS = {path: _font_cmap(path) for path in (FONT_BOLD, FONT_REGULAR)}


def sanitize_text(text: str, *fonts: str, fallback: str = "-") -> str:
    """Remplace les caractères absents de tous les `fonts` donnés (glyphe .notdef,
    typiquement un carré vide) par `fallback`, pour éviter un rendu cassé."""
    cmaps = [_CMAPS.get(f, set()) for f in fonts]
    if not any(cmaps):
        return text
    out = []
    for ch in text:
        if ch == " " or any(ord(ch) in cmap for cmap in cmaps):
            out.append(ch)
        else:
            print(f"⚠ Caractère non supporté par la police, remplacé : {ch!r}", file=sys.stderr)
            out.append(fallback)
    return "".join(out)

MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def format_date_fr(d: date) -> str:
    return f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def photo_date(photo: Path) -> date | None:
    """Date de prise de vue (EXIF, repli sur le nom de fichier YYYYMMDD_HHMMSS)."""
    try:
        exif = Image.open(photo).getexif()
        exif_ifd = exif.get_ifd(0x8769)
        raw = exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)
        if raw:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").date()
    except Exception:
        pass

    m = re.match(r"(\d{8})_(\d{6})", photo.stem)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").date()
    return None


WHITE = (255, 255, 255, 255)
BLACK = (20, 20, 20, 255)


def parse_color(raw: str) -> tuple[int, int, int, int]:
    """Accepte un nom de couleur PIL ('orange') ou un hexa ('#FF6600')."""
    r, g, b = ImageColor.getrgb(raw)
    return (r, g, b, 255)


def dim(color: tuple[int, int, int, int], alpha: int = 200) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def contrasting_text_color(bg: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Retourne noir ou blanc selon la couleur qui contraste le mieux avec `bg`."""
    r, g, b = bg[:3]
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return BLACK if luminance > 0.55 else WHITE


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def fit_font(path: str, text: str, max_width: int, start_size: int, min_size: int = 8) -> ImageFont.FreeTypeFont:
    """Réduit la taille de police jusqu'à ce que `text` tienne dans `max_width`."""
    size = start_size
    font = load_font(path, size)
    while size > min_size:
        bbox = font.getbbox(text)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            break
        size -= 2
        font = load_font(path, size)
    return font


def draw_tracked_text(draw: ImageDraw.ImageDraw, xy, text: str, font: ImageFont.FreeTypeFont,
                       fill, tracking: float = 0.0):
    """Dessine du texte avec un espacement inter-lettres (tracking) manuel."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        bbox = font.getbbox(ch)
        x += (bbox[2] - bbox[0]) + tracking
    return x


def tracked_text_width(text: str, font: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    width = 0.0
    for ch in text:
        bbox = font.getbbox(ch)
        width += (bbox[2] - bbox[0]) + tracking
    return max(0.0, width - tracking)


def add_vertical_vignette(base: Image.Image, top_h_frac: float, bottom_h_frac: float,
                           max_alpha: int = 130) -> Image.Image:
    """Assombrit légèrement le haut et le bas de l'image pour la lisibilité du texte."""
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    top_h = int(h * top_h_frac)
    if top_h > 0:
        top_grad = Image.linear_gradient("L").resize((w, top_h))
        top_grad = top_grad.transpose(Image.FLIP_TOP_BOTTOM)  # noir en haut -> transparent
        top_grad = top_grad.point(lambda p: int(p / 255 * max_alpha))
        black_top = Image.new("RGBA", (w, top_h), (0, 0, 0, 255))
        overlay.paste(black_top, (0, 0), top_grad)

    bottom_h = int(h * bottom_h_frac)
    if bottom_h > 0:
        bottom_grad = Image.linear_gradient("L").resize((w, bottom_h))
        bottom_grad = bottom_grad.point(lambda p: int(p / 255 * max_alpha))
        black_bottom = Image.new("RGBA", (w, bottom_h), (0, 0, 0, 255))
        overlay.paste(black_bottom, (0, h - bottom_h), bottom_grad)

    return Image.alpha_composite(base, overlay)


def draw_scrim(img: Image.Image, box: tuple[float, float, float, float], radius: int, alpha: int = 130):
    """Voile sombre arrondi derrière un bloc de texte, pour garantir la lisibilité."""
    w, h = img.size
    x0, y0, x1, y1 = box
    scrim = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(scrim).rounded_rectangle(
        [max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)], radius=radius, fill=(0, 0, 0, alpha))
    img.alpha_composite(scrim)


def draw_title_zone(img: Image.Image, draw: ImageDraw.ImageDraw, titre: str, date_heure: str, lieu: str,
                     color: tuple[int, int, int, int] = WHITE):
    w, h = img.size
    base = min(w, h)
    margin_x = int(base * 0.045)
    top_y = int(h * 0.035)

    title_size = max(18, int(base * 0.052))
    title_font = fit_font(FONT_BOLD, titre, int(w * 0.75), title_size)
    tbbox = title_font.getbbox(titre)
    title_w = tbbox[2] - tbbox[0]
    title_h = tbbox[3] - tbbox[1]

    sub_size = max(12, int(title_size * 0.45))
    sub_font = load_font(FONT_REGULAR, sub_size)
    sub_text = f"{date_heure} @ {lieu}" if date_heure else lieu
    sbbox = sub_font.getbbox(sub_text)
    sub_w = sbbox[2] - sbbox[0]
    sub_h = sbbox[3] - sbbox[1]
    sub_y = top_y + title_h + int(h * 0.012)

    pad_x = int(base * 0.035)
    pad_y = int(base * 0.025)
    content_w = max(title_w, sub_w)
    draw_scrim(img, (margin_x - pad_x, top_y - pad_y, margin_x + content_w + pad_x, sub_y + sub_h + pad_y), pad_x)

    draw.text((margin_x, top_y), titre, font=title_font, fill=color)
    draw.text((margin_x, sub_y), sub_text, font=sub_font, fill=dim(color))


def draw_logo_zone(img: Image.Image, event_name: str, box_color: tuple[int, int, int, int] = WHITE):
    w, h = img.size
    base = min(w, h)
    box_h = int(h * 0.20)
    box_w = int(box_h * 0.24)  # largeur proportionnelle à la hauteur du pavé (constante quelle que soit l'orientation)
    inner_pad = int(box_h * 0.06)
    text_color = contrasting_text_color(box_color)

    # Rendu horizontal du texte sur un calque séparé (taille ajustée pour tenir,
    # une fois pivoté, dans la hauteur du pavé), puis rotation -90°.
    avail_for_text_width = max(10, box_h - 2 * inner_pad)
    txt_font = fit_font(FONT_BOLD, event_name, avail_for_text_width, int(base * 0.09))
    bbox = txt_font.getbbox(event_name)
    txt_w = bbox[2] - bbox[0]
    txt_h = bbox[3] - bbox[1]

    layer = Image.new("RGBA", (max(txt_w, 1), max(txt_h, 1)), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    ldraw.text((-bbox[0], -bbox[1]), event_name, font=txt_font, fill=text_color)
    rotated = layer.rotate(90, expand=True)

    box = Image.new("RGBA", (box_w, box_h), box_color)
    bx = max((box_w - rotated.width) // 2, 0)
    by = max((box_h - rotated.height) // 2, 0)
    box.alpha_composite(rotated, (bx, by))

    top_y = int(h * 0.035)  # aligné sur le point haut du titre (cf. draw_title_zone)
    img.alpha_composite(box, (w - box_w, top_y))


def draw_stats_zone(img: Image.Image, draw: ImageDraw.ImageDraw, stats: list[tuple[str, str, str]],
                     color: tuple[int, int, int, int] = WHITE, position: str = "gauche"):
    w, h = img.size
    base = min(w, h)
    margin_x = int(base * 0.045)
    margin_bottom = int(h * 0.045)
    align_right = position == "droite"

    label_size = max(10, int(base * 0.02))
    value_size = max(20, int(base * 0.04))
    unit_size = max(10, int(value_size * 0.45))

    label_font = load_font(FONT_REGULAR, label_size)
    value_font = load_font(FONT_BOLD, value_size)
    unit_font = load_font(FONT_BOLD, unit_size)

    label_gap = int(label_size * 0.9)
    inter_stat_gap = int(h * 0.016)
    unit_sep = int(base * 0.008)

    row_heights = []
    for label, value, unit in stats:
        lb = label_font.getbbox(label)
        vb = value_font.getbbox(value)
        row_h = (lb[3] - lb[1]) + label_gap + (vb[3] - vb[1])
        row_heights.append(row_h)

    total_h = sum(row_heights) + inter_stat_gap * (len(stats) - 1 if stats else 0)
    y = h - margin_bottom - total_h

    tracking = max(1, int(label_size * 0.18))

    # Voile sombre derrière le bloc stats pour garantir la lisibilité, quelle que
    # soit la photo ou la couleur d'accent choisie.
    content_w = 0.0
    for label, value, unit in stats:
        label_w = tracked_text_width(label, label_font, tracking=tracking)
        vb = value_font.getbbox(value)
        v_w = vb[2] - vb[0]
        unit_w = 0
        if unit:
            ub = unit_font.getbbox(unit)
            unit_w = unit_sep + (ub[2] - ub[0])
        content_w = max(content_w, label_w, v_w + unit_w)

    pad_x = int(base * 0.035)
    pad_y = int(base * 0.025)
    scrim_x0 = (w - margin_x - content_w - pad_x) if align_right else (margin_x - pad_x)
    scrim_x1 = (w - margin_x + pad_x) if align_right else (margin_x + content_w + pad_x)
    draw_scrim(img, (scrim_x0, y - pad_y, scrim_x1, (h - margin_bottom) + pad_y), pad_x)

    for (label, value, unit), row_h in zip(stats, row_heights):
        lb = label_font.getbbox(label)
        label_w = tracked_text_width(label, label_font, tracking=tracking)
        label_x = (w - margin_x - label_w) if align_right else margin_x
        draw_tracked_text(draw, (label_x, y), label, label_font, dim(color), tracking=tracking)
        y += (lb[3] - lb[1]) + label_gap

        vb = value_font.getbbox(value)
        v_w = vb[2] - vb[0]
        unit_w = 0
        if unit:
            ub = unit_font.getbbox(unit)
            unit_w = unit_sep + (ub[2] - ub[0])
        group_w = v_w + unit_w
        value_x = (w - margin_x - group_w) if align_right else margin_x
        draw.text((value_x, y), value, font=value_font, fill=color)
        if unit:
            draw.text((value_x + v_w + unit_sep, y + int(value_size * 0.35)),
                      unit, font=unit_font, fill=color)
        y += (vb[3] - vb[1]) + inter_stat_gap


def parse_stat(raw: str) -> tuple[str, str, str]:
    parts = raw.split("|")
    while len(parts) < 3:
        parts.append("")
    label, value, unit = parts[0].strip(), parts[1].strip(), parts[2].strip()
    return label.upper(), value, unit


IMG_EXTS = {".jpg", ".jpeg", ".png"}
NO_DATE_KEY = "sans-date"


def collect_photos(in_dir: Path) -> list[Path]:
    """`in_dir` peut être une photo unique ou un dossier (traité en masse)."""
    if in_dir.is_file():
        return [in_dir]
    if in_dir.is_dir():
        return sorted(p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS)
    return []


def group_by_date(photos: list[Path]) -> dict[str, list[Path]]:
    """Regroupe les photos par date de prise de vue (clé ISO 'AAAA-MM-JJ'),
    'sans-date' pour celles dont la date n'a pas pu être déterminée."""
    groups: dict[str, list[Path]] = {}
    for photo in photos:
        d = photo_date(photo)
        key = d.isoformat() if d else NO_DATE_KEY
        groups.setdefault(key, []).append(photo)
    return groups


def render_one(photo: Path, out_path: Path, event_name: str, titre: str, date_heure: str, lieu: str,
               stats: list[tuple[str, str, str]], accent: tuple[int, int, int, int], position: str):
    titre = sanitize_text(titre, FONT_BOLD)
    event_name = sanitize_text(event_name, FONT_BOLD)
    lieu = sanitize_text(lieu, FONT_REGULAR)
    date_heure = sanitize_text(date_heure, FONT_REGULAR)
    stats = [(sanitize_text(l, FONT_REGULAR), sanitize_text(v, FONT_BOLD), sanitize_text(u, FONT_BOLD))
             for l, v, u in stats]

    img = Image.open(photo)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGBA")

    img = add_vertical_vignette(img, top_h_frac=0.22, bottom_h_frac=0.32)
    draw = ImageDraw.Draw(img)

    draw_title_zone(img, draw, titre, date_heure, lieu, color=accent)
    draw_logo_zone(img, event_name, box_color=accent)

    if stats:
        draw_stats_zone(img, draw, stats, color=accent, position=position)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, quality=95)


def main():
    parser = argparse.ArgumentParser(description="Compose un habillage type Garmin sur une ou plusieurs photos.")
    parser.add_argument("--in-dir", required=True, type=Path,
                         help="Photo unique ou dossier de photos à traiter (traitement de masse si dossier)")
    parser.add_argument("--out-dir", type=Path, help="Dossier de sortie (requis hors --list-dates)")
    parser.add_argument("--event-name", default="GARMIN", help="Nom d'événement (pavé blanc en haut à droite)")
    parser.add_argument("--titre", default=None, help="Titre de l'activité (mode uniforme, sans --manifest)")
    parser.add_argument("--date-heure", default=None,
                         help="Date, ex. '2 mai 2026'. Si omis, dérivée automatiquement de l'EXIF de "
                              "chaque photo (repli sur le nom de fichier YYYYMMDD_HHMMSS).")
    parser.add_argument("--lieu", default=None, help="Lieu de l'activité (mode uniforme, sans --manifest)")
    parser.add_argument("--stat", action="append", default=[], metavar="LABEL|VALEUR|UNITE",
                         help="Statistique à afficher, répétable (mode uniforme). Ex. --stat 'DISTANCE|27,13|km'")
    parser.add_argument("--couleur", default=None,
                         help="Couleur d'accent (nom PIL ou hexa '#FF6600'), appliquée au fond du pavé "
                              "événement, au titre et aux stats. Le texte du nom d'événement bascule "
                              "automatiquement en noir ou blanc selon le contraste. Défaut : blanc / fond noir.")
    parser.add_argument("--manifest", type=Path, default=None,
                         help="JSON {date_iso: {titre, lieu, stat: [...]}} — un habillage par date de prise "
                              "de vue plutôt qu'un habillage uniforme. Prioritaire sur --titre/--lieu/--stat.")
    parser.add_argument("--list-dates", action="store_true",
                         help="N'affiche que les dates détectées dans --in-dir (avec leur statut dans "
                              "--manifest s'il est fourni) et quitte, sans rien générer.")

    args = parser.parse_args()

    photos = collect_photos(args.in_dir)
    if not photos:
        print(f"✗ Aucune photo trouvée : {args.in_dir}", file=sys.stderr)
        sys.exit(1)

    if args.list_dates:
        manifest_keys = set()
        if args.manifest and args.manifest.exists():
            manifest_keys = set(json.loads(args.manifest.read_text()).keys())
        groups = group_by_date(photos)
        for key in sorted(groups):
            statut = "présent" if key in manifest_keys else "absent"
            noms = ", ".join(p.name for p in groups[key])
            print(f"{key} | {len(groups[key])} photo(s) | manifest: {statut} | {noms}")
        return

    if not args.out_dir:
        print("✗ --out-dir est requis (hors --list-dates)", file=sys.stderr)
        sys.exit(1)

    accent = parse_color(args.couleur) if args.couleur else WHITE
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.manifest:
        if not args.manifest.exists():
            print(f"✗ Manifest introuvable : {args.manifest}", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(args.manifest.read_text())
        groups = group_by_date(photos)
        missing = [k for k in groups if k not in manifest]
        if missing:
            print(f"✗ Dates absentes du manifest : {', '.join(sorted(missing))}", file=sys.stderr)
            sys.exit(1)

        for key in sorted(groups):
            entry = manifest[key]
            titre = entry["titre"]
            lieu = entry["lieu"]
            stats = [parse_stat(s) for s in entry.get("stat", [])]
            date_heure = format_date_fr(date.fromisoformat(key)) if key != NO_DATE_KEY else ""
            for photo in groups[key]:
                for position in ("gauche", "droite"):
                    out_path = args.out_dir / f"{photo.stem}-{position}.png"
                    render_one(photo, out_path, args.event_name, titre, date_heure, lieu,
                               stats, accent, position)
                    print(f"✓ Image : {out_path}")
        return

    if not args.titre or not args.lieu:
        print("✗ --titre et --lieu sont requis (ou fournir --manifest)", file=sys.stderr)
        sys.exit(1)

    stats = [parse_stat(s) for s in args.stat]

    for photo in photos:
        d = photo_date(photo)
        date_heure = args.date_heure or (format_date_fr(d) if d else "")
        if not date_heure:
            print(f"⚠ Date introuvable (EXIF et nom de fichier) pour {photo.name} — sous-titre sans date", file=sys.stderr)
        for position in ("gauche", "droite"):
            out_path = args.out_dir / f"{photo.stem}-{position}.png"
            render_one(photo, out_path, args.event_name, args.titre, date_heure, args.lieu,
                       stats, accent, position)
            print(f"✓ Image : {out_path}")


if __name__ == "__main__":
    main()

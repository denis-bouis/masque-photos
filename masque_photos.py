#!/usr/bin/env python3
"""
Masque Photos — compose un habillage type Garmin (titre, logo/événement,
statistiques) par-dessus une ou plusieurs photos.

Prérequis : pip install Pillow fontTools opencv-python-headless
(opencv est optionnel — seulement nécessaire pour la détection de visage qui
évite au tracé GPS de les traverser ; son absence dégrade silencieusement vers
la position par défaut du tracé)

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
import math
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

FONT_BOLD = "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"
FONT_REGULAR = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"
FONT_SIGNATURE_PATH = "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc"
FONT_SIGNATURE_INDEX = 1  # 0=Regular, 1=Bold, 2=Black — Bold reste lisible sur photo sans bloc derrière


def _font_cmap(path: str) -> set[int]:
    try:
        from fontTools.ttLib import TTFont
        return set(TTFont(path).getBestCmap().keys())
    except Exception:
        return set()


_CMAPS = {path: _font_cmap(path) for path in (FONT_BOLD, FONT_REGULAR, FONT_SIGNATURE_PATH)}


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


def photo_datetime(photo: Path) -> datetime | None:
    """Date et heure de prise de vue (EXIF, repli sur le nom de fichier YYYYMMDD_HHMMSS)."""
    try:
        exif = Image.open(photo).getexif()
        exif_ifd = exif.get_ifd(0x8769)
        raw = exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)
        if raw:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass

    m = re.match(r"(\d{8})_(\d{6})", photo.stem)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    return None


def photo_date(photo: Path) -> date | None:
    """Date de prise de vue (EXIF, repli sur le nom de fichier YYYYMMDD_HHMMSS)."""
    dt = photo_datetime(photo)
    return dt.date() if dt else None


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


def load_font(path: str, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=index)


def fit_font(path: str, text: str, max_width: int, start_size: int, min_size: int = 8,
             index: int = 0) -> ImageFont.FreeTypeFont:
    """Réduit la taille de police jusqu'à ce que `text` tienne dans `max_width`."""
    size = start_size
    font = load_font(path, size, index=index)
    while size > min_size:
        bbox = font.getbbox(text)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            break
        size -= 2
        font = load_font(path, size, index=index)
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


INSTAGRAM_MIN_RATIO, INSTAGRAM_MAX_RATIO = 0.5625, 1.91  # portrait 9:16 (reels/stories), paysage 1.91:1


def clamp_aspect_for_instagram(img: Image.Image) -> Image.Image:
    """Recadre au ratio maximum accepté par Instagram (portrait 9:16 — la limite verticale la
    plus permissive sur toute la plateforme, reels/stories compris ; paysage max 1.91:1),
    centré. Sans ce recadrage, une photo hors de ces bornes serait recadrée par Instagram
    lui-même à la publication — de façon imprévisible, risquant de couper le bandeau titre ou
    le bloc stats incrustés près des bords de l'image."""
    w, h = img.size
    ratio = w / h
    if ratio < INSTAGRAM_MIN_RATIO:
        new_h = round(w / INSTAGRAM_MIN_RATIO)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))
    if ratio > INSTAGRAM_MAX_RATIO:
        new_w = round(h * INSTAGRAM_MAX_RATIO)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    return img


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


def draw_title_only_zone(img: Image.Image, draw: ImageDraw.ImageDraw, titre: str,
                          color: tuple[int, int, int, int] = WHITE):
    """Variante de draw_title_zone sans le sous-titre date/lieu (mode adaptatif, rôle -titre)."""
    w, h = img.size
    base = min(w, h)
    margin_x = int(base * 0.045)
    top_y = int(h * 0.035)

    title_size = max(18, int(base * 0.052))
    title_font = fit_font(FONT_BOLD, titre, int(w * 0.75), title_size)
    tbbox = title_font.getbbox(titre)
    title_w = tbbox[2] - tbbox[0]
    title_h = tbbox[3] - tbbox[1]

    pad_x = int(base * 0.035)
    pad_y = int(base * 0.025)
    draw_scrim(img, (margin_x - pad_x, top_y - pad_y, margin_x + title_w + pad_x, top_y + title_h + pad_y), pad_x)

    draw.text((margin_x, top_y), titre, font=title_font, fill=color)


def draw_logo_zone(img: Image.Image, event_name: str, box_color: tuple[int, int, int, int] = WHITE):
    w, h = img.size
    base = min(w, h)
    margin_x = int(base * 0.045)  # même marge que le bloc stats, pour un alignement droit commun
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
    img.alpha_composite(box, (w - margin_x - box_w, top_y))


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


def apply_scrim_overlay(img: Image.Image, alpha: int = 127,
                         rgb: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """Estompe toute l'image d'un voile semi-transparent uni (mode adaptatif, rôle -citation) —
    `alpha` sur 255 (127 ≈ 50%), `rgb` la couleur du voile (noir par défaut)."""
    overlay = Image.new("RGBA", img.size, (*rgb, alpha))
    return Image.alpha_composite(img, overlay)


def wrap_text_lines(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Découpe `text` en lignes tenant chacune dans `max_width` (retour à la ligne mot par
    mot, glouton). Césure uniquement sur l'espace normal (" ") — une espace insécable
    ( ) dans `text` reste dans le même "mot" et n'est donc jamais coupée en fin de
    ligne (utile pour garder un guillemet français « » collé au mot voisin)."""
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = font.getbbox(candidate)
        if not current or bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_citation_font(text: str, max_width: float, max_height: float, start_size: int,
                       min_size: int = 24) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Réduit la taille de police jusqu'à ce que `text`, réparti sur plusieurs lignes
    (glouton, cf. `wrap_text_lines`), tienne dans `max_width` x `max_height`. Police cursive
    (Snell Roundhand, même style que la signature -- cf. draw_signature_zone)."""
    size = start_size
    while size > min_size:
        font = load_font(FONT_SIGNATURE_PATH, size, index=FONT_SIGNATURE_INDEX)
        lines = wrap_text_lines(text, font, max_width)
        lb = font.getbbox("Hg")
        line_h = lb[3] - lb[1]
        line_gap = int(size * 0.25)
        total_h = len(lines) * line_h + (len(lines) - 1) * line_gap
        if total_h <= max_height:
            return font, lines
        size -= 4
    font = load_font(FONT_SIGNATURE_PATH, min_size, index=FONT_SIGNATURE_INDEX)
    return font, wrap_text_lines(text, font, max_width)


def draw_citation_zone(img: Image.Image, draw: ImageDraw.ImageDraw, citation: str,
                        color: tuple[int, int, int, int] = WHITE):
    """Citation entre chevrons français, centrée horizontalement et verticalement, répartie
    sur plusieurs lignes si nécessaire — même style manuscrit que la signature (mode adaptatif,
    rôle -citation) : police cursive, pas de bloc/voile derrière, juste une ombre portée légère
    pour la lisibilité."""
    w, h = img.size
    base = min(w, h)
    text = f"\u00ab {citation} \u00bb"
    max_width = w * 0.8
    max_height = h * 0.5
    start_size = max(24, int(base * 0.055))
    font, lines = fit_citation_font(text, max_width, max_height, start_size)

    lb = font.getbbox("Hg")
    line_h = lb[3] - lb[1]
    line_gap = int(font.size * 0.25)
    total_h = len(lines) * line_h + (len(lines) - 1) * line_gap

    shadow = max(1, int(base * 0.003))
    y = (h - total_h) / 2
    for line in lines:
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x = (w - line_w) / 2
        draw.text((x + shadow, y + shadow), line, font=font, fill=(0, 0, 0, 140))
        draw.text((x, y), line, font=font, fill=color)
        y += line_h + line_gap


def load_gpx_track(path: Path) -> list[tuple[float, float]]:
    """Points (lat, lon) d'un fichier GPX, dans l'ordre du tracé."""
    tree = ET.parse(path)
    points = []
    for trkpt in tree.getroot().iter():
        if trkpt.tag.endswith("trkpt"):
            points.append((float(trkpt.attrib["lat"]), float(trkpt.attrib["lon"])))
    if len(points) < 2:
        raise ValueError(f"Tracé GPX vide ou insuffisant : {path}")
    return points


def gpx_first_date(path: Path) -> date | None:
    """Date (UTC, sans repli fuseau horaire) du premier point d'un fichier GPX."""
    tree = ET.parse(path)
    for trkpt in tree.getroot().iter():
        if trkpt.tag.endswith("trkpt"):
            for child in trkpt:
                if child.tag.endswith("time"):
                    return datetime.fromisoformat(child.text.replace("Z", "+00:00")).date()
    return None


def gpx_track_name(path: Path) -> str | None:
    """Nom de piste (<trk><name>) d'un fichier GPX exporté par Garmin Connect, si
    présent — reprend le nom donné à l'activité (souvent déjà descriptif)."""
    tree = ET.parse(path)
    for trk in tree.getroot():
        if trk.tag.endswith("trk"):
            for child in trk:
                if child.tag.endswith("name") and child.text and child.text.strip():
                    return child.text.strip()
    return None


def match_gpx_files(gpx_dir: Path, manifest_dir: Path, photo_dates: set[str]) -> dict[str, list[str]]:
    """Associe les fichiers .gpx de `gpx_dir` aux dates ISO (parmi `photo_dates`) de
    leur premier point, en chemins relatifs à `manifest_dir`. Une date peut recevoir
    plusieurs fichiers (ex. deux activités le même jour, non contiguës) — tous sont
    conservés, dans l'ordre. Un GPX dont la date ne correspond à aucune date de photo
    est ignoré (rapprochement manuel si besoin)."""
    mapping: dict[str, list[str]] = {}
    if not gpx_dir.is_dir():
        return mapping
    for gpx in sorted(gpx_dir.glob("*.gpx")):
        d = gpx_first_date(gpx)
        if d and d.isoformat() in photo_dates:
            mapping.setdefault(d.isoformat(), []).append(os.path.relpath(gpx, manifest_dir))
    return mapping


def project_tracks(segments: list[list[tuple[float, float]]], box_w: float, box_h: float,
                    pad_frac: float = 0.1) -> list[list[tuple[float, float]]]:
    """Projette plusieurs tracés lat/lon (ex. deux activités distinctes le même jour)
    dans une boîte de box_w x box_h pixels partagée (coordonnées relatives à l'origine
    de la boîte), en conservant les proportions réelles à une échelle commune — la
    longitude est corrigée par cos(latitude moyenne) pour éviter toute déformation
    est-ouest. Chaque segment reste une polyligne séparée (pas de trait de liaison
    entre deux tracés distincts)."""
    all_points = [p for seg in segments for p in seg]
    lats = [p[0] for p in all_points]
    lons = [p[1] for p in all_points]
    lon_scale = math.cos(math.radians(sum(lats) / len(lats)))

    xs = [lon * lon_scale for lon in lons]
    ys = [-lat for lat in lats]  # inverser : latitude croissante -> y décroissant (repère écran)

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)

    avail_w = box_w * (1 - 2 * pad_frac)
    avail_h = box_h * (1 - 2 * pad_frac)
    scale = min(avail_w / span_x, avail_h / span_y)

    draw_w, draw_h = span_x * scale, span_y * scale
    offset_x = (box_w - draw_w) / 2
    offset_y = (box_h - draw_h) / 2

    out = []
    for seg in segments:
        pts = []
        for lat, lon in seg:
            x, y = lon * lon_scale, -lat
            pts.append((offset_x + (x - min_x) * scale, offset_y + (y - min_y) * scale))
        out.append(pts)
    return out


def _draw_track_segment(ldraw: ImageDraw.ImageDraw, pts: list[tuple[float, float]],
                         color: tuple[int, int, int, int], line_w: int):
    """Dessine une polyligne + point de départ + flèche d'arrivée pour un seul segment."""
    shadow_w = line_w + max(2, round(line_w * 1.8))
    ldraw.line(pts, fill=(0, 0, 0, 110), width=shadow_w, joint="curve")
    ldraw.line(pts, fill=color, width=line_w, joint="curve")

    dot_r = line_w * 1.9
    sx, sy = pts[0]
    ldraw.ellipse([sx - dot_r, sy - dot_r, sx + dot_r, sy + dot_r], fill=(0, 0, 0, 110))
    ldraw.ellipse([sx - dot_r * 0.75, sy - dot_r * 0.75, sx + dot_r * 0.75, sy + dot_r * 0.75], fill=color)

    ex, ey = pts[-1]
    px, py = next(((x, y) for x, y in reversed(pts[:-1]) if (x, y) != (ex, ey)), (ex - 1, ey))
    dx, dy = ex - px, ey - py
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    perp_x, perp_y = -dy, dx
    arrow_len, arrow_w = dot_r * 3.0, dot_r * 1.8
    bx, by = ex - dx * arrow_len, ey - dy * arrow_len
    triangle = [(ex, ey), (bx + perp_x * arrow_w, by + perp_y * arrow_w),
                (bx - perp_x * arrow_w, by - perp_y * arrow_w)]
    ldraw.polygon(triangle, fill=color)


def draw_gps_track_zone(img: Image.Image, segments: list[list[tuple[float, float]]],
                         color: tuple[int, int, int, int] = WHITE, position: str = "gauche"):
    """Dessine un ou plusieurs tracés GPS stylisés (ex. deux activités distinctes le
    même jour) : ligne + point de départ + flèche d'arrivée par segment, à une échelle
    commune, dans un bloc en bas de l'image — du côté **opposé** au bloc stats (bas
    gauche si `position` "droite", bas droite si "gauche"), pour que les 4 coins de
    l'image portent chacun un bloc distinct (titre, logo, stats, tracé). Les tracés
    sont mis à l'échelle pour tenir dans leur boîte, pas projetés sur le terrain réel
    de la photo (aucune donnée de pose de caméra disponible pour un ancrage fidèle)."""
    w, h = img.size
    base = min(w, h)
    margin_x = int(base * 0.045)
    margin_bottom = int(h * 0.045)
    box_w = int(w * 0.42)
    box_h = int(h * 0.18)

    origin_x = margin_x if position == "droite" else w - margin_x - box_w
    origin_y = h - margin_bottom - box_h

    rel_segments = project_tracks(segments, box_w, box_h)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    line_w = max(2, round(base * 0.006))

    for rel_points in rel_segments:
        pts = [(origin_x + x, origin_y + y) for x, y in rel_points]
        _draw_track_segment(ldraw, pts, color, line_w)

    img.alpha_composite(layer)


def parse_stat(raw: str) -> tuple[str, str, str]:
    parts = raw.split("|")
    while len(parts) < 3:
        parts.append("")
    label, value, unit = parts[0].strip(), parts[1].strip(), parts[2].strip()
    return label.upper(), value, unit


IMG_EXTS = {".jpg", ".jpeg", ".png"}
NO_DATE_KEY = "sans-date"
OUTPUT_SUFFIXES = ("-gauche", "-droite")  # suffixes exclusifs des sorties mode single, jamais des sources brutes


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


def _prepare_image(photo: Path) -> Image.Image:
    """Ouvre `photo`, corrige l'orientation EXIF, convertit en RGBA et recadre au ratio
    Instagram — étapes communes à tous les modes de rendu."""
    img = Image.open(photo)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGBA")
    return clamp_aspect_for_instagram(img)


def _save_image(img: Image.Image, out_path: Path, photo: Path):
    """Sauvegarde `img` en JPEG dans `out_path` et reprend la date EXIF de `photo` sur le
    fichier généré — étapes communes à tous les modes de rendu."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, quality=95)

    dt = photo_datetime(photo)
    if dt:
        ts = dt.timestamp()
        os.utime(out_path, (ts, ts))


def draw_signature_zone(img: Image.Image, draw: ImageDraw.ImageDraw, text: str,
                         color: tuple[int, int, int, int] = WHITE):
    """Signature discrète en bas à droite — seul marquage des photos non surchargées (mode
    adaptatif, aucun suffixe reconnu). Pas de bloc/voile derrière le texte (contrairement aux
    autres zones) : rendu en police cursive (Snell Roundhand) façon signature manuscrite, avec
    une simple ombre portée pour rester lisible sans assombrir la photo."""
    w, h = img.size
    base = min(w, h)
    margin_x = int(base * 0.045)
    margin_bottom = int(h * 0.045)

    size = max(24, int(base * 0.05))
    font = fit_font(FONT_SIGNATURE_PATH, text, int(w * 0.6), size, index=FONT_SIGNATURE_INDEX)
    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = w - margin_x - text_w
    y = h - margin_bottom - text_h
    shadow = max(1, int(base * 0.003))
    draw.text((x + shadow, y + shadow), text, font=font, fill=(0, 0, 0, 140))
    draw.text((x, y), text, font=font, fill=color)


def render_signature(photo: Path, out_path: Path, signature: str, accent: tuple[int, int, int, int]):
    """Rôle par défaut en mode adaptatif pour une photo sans suffixe reconnu : aucun habillage
    informatif (pas de titre/événement/stats/tracé/vignette) — juste la signature du compte en
    bas à droite, pour les photos brutes de carrousel qu'on ne veut pas surcharger."""
    signature = sanitize_text(signature, FONT_SIGNATURE_PATH)
    img = _prepare_image(photo)
    draw = ImageDraw.Draw(img)
    draw_signature_zone(img, draw, signature, color=accent)
    _save_image(img, out_path, photo)


def render_one(photo: Path, out_path: Path, event_name: str, titre: str, date_heure: str, lieu: str,
               stats: list[tuple[str, str, str]], accent: tuple[int, int, int, int], position: str,
               track: list[list[tuple[float, float]]] | None = None):
    titre = sanitize_text(titre, FONT_BOLD)
    event_name = sanitize_text(event_name, FONT_BOLD)
    lieu = sanitize_text(lieu, FONT_REGULAR)
    date_heure = sanitize_text(date_heure, FONT_REGULAR)
    stats = [(sanitize_text(l, FONT_REGULAR), sanitize_text(v, FONT_BOLD), sanitize_text(u, FONT_BOLD))
             for l, v, u in stats]

    img = _prepare_image(photo)
    img = add_vertical_vignette(img, top_h_frac=0.22, bottom_h_frac=0.32)

    if track:
        draw_gps_track_zone(img, track, color=accent, position=position)

    draw = ImageDraw.Draw(img)

    draw_title_zone(img, draw, titre, date_heure, lieu, color=accent)
    draw_logo_zone(img, event_name, box_color=accent)

    if stats:
        draw_stats_zone(img, draw, stats, color=accent, position=position)

    _save_image(img, out_path, photo)


def render_scene(photo: Path, out_path: Path, event_name: str, titre: str, date_heure: str, lieu: str,
                  stats: list[tuple[str, str, str]], accent: tuple[int, int, int, int],
                  track: list[list[tuple[float, float]]] | None = None, position: str = "droite"):
    """Rôle -scene (mode adaptatif) : identité complète du post sur une seule photo — nom
    d'événement, titre, date, lieu, statistiques et tracé GPS. Seul rôle indispensable : son
    absence pour une date/un post est signalée. Même contenu que le mode single (`render_one`)
    mais une seule image en sortie (pas de variante gauche/droite), pour une photo déjà cadrée
    par Denis. `position` choisit le côté du bloc stats (bas droite par défaut) ; le tracé va
    systématiquement au coin bas opposé."""
    render_one(photo, out_path, event_name, titre, date_heure, lieu, stats, accent, position, track=track)


def render_titre(photo: Path, out_path: Path, titre: str, accent: tuple[int, int, int, int]):
    """Rôle -titre (mode adaptatif) : titre seul — sans nom d'événement, lieu, stats ni tracé."""
    titre = sanitize_text(titre, FONT_BOLD)

    img = _prepare_image(photo)
    img = add_vertical_vignette(img, top_h_frac=0.22, bottom_h_frac=0.32)

    draw = ImageDraw.Draw(img)
    draw_title_only_zone(img, draw, titre, color=accent)

    _save_image(img, out_path, photo)


def render_stat(photo: Path, out_path: Path, stats: list[tuple[str, str, str]],
                 accent: tuple[int, int, int, int], track: list[list[tuple[float, float]]] | None = None,
                 position: str = "gauche"):
    """Rôle -stat (mode adaptatif) : statistiques et tracé GPS — sans titre ni nom d'événement."""
    stats = [(sanitize_text(l, FONT_REGULAR), sanitize_text(v, FONT_BOLD), sanitize_text(u, FONT_BOLD))
             for l, v, u in stats]

    img = _prepare_image(photo)
    img = add_vertical_vignette(img, top_h_frac=0.22, bottom_h_frac=0.32)

    if track:
        draw_gps_track_zone(img, track, color=accent, position=position)

    draw = ImageDraw.Draw(img)
    if stats:
        draw_stats_zone(img, draw, stats, color=accent, position=position)

    _save_image(img, out_path, photo)


def render_citation(photo: Path, out_path: Path, citation: str, accent: tuple[int, int, int, int],
                     voile: bool = False, signature: str | None = None):
    """Rôle -citation (mode adaptatif, optionnel) : citation entre chevrons, centrée, répartie
    sur plusieurs lignes si nécessaire — même style manuscrit que la signature (police cursive,
    pas de bloc/voile derrière le texte). `voile` (option par date, clé 'citation_voile' du
    manifest) applique un voile blanc à 50% sur toute la photo — utile si une photo trop
    chargée/claire nuit au contraste, plus nécessaire qu'avant vu l'absence de pavé plein
    derrière le texte. `signature` (optionnel, même source que --signature/clé manifest) ajoute
    en plus la signature du compte en bas à droite, comme sur les photos non surchargées."""
    citation = sanitize_text(citation, FONT_SIGNATURE_PATH)
    img = _prepare_image(photo)
    if voile:
        img = apply_scrim_overlay(img, alpha=127, rgb=(255, 255, 255))

    draw = ImageDraw.Draw(img)
    draw_citation_zone(img, draw, citation, color=accent)
    if signature:
        signature = sanitize_text(signature, FONT_SIGNATURE_PATH)
        draw_signature_zone(img, draw, signature, color=accent)

    _save_image(img, out_path, photo)


PHOTO_ROLES = ("scene", "titre", "stat", "citation")


def photo_role(photo: Path) -> str | None:
    """Rôle d'une photo en mode adaptatif, d'après son nom de fichier : suffixe '-scene'
    (identité complète du post : événement/titre/date/lieu/stats/tracé, seul rôle indispensable),
    '-titre' (titre seul), '-stat' (stats + tracé, sans événement/date/lieu) ou '-citation'
    (citation seule) — tous optionnels
    sauf -scene. None si aucun de ces suffixes n'est présent : la photo n'est alors pas surchargée (cf.
    render_adaptive — signature seule, ou rien si aucune signature fournie). La date reste
    extraite normalement par `photo_date` (regex ancrée en début de nom, insensible au
    suffixe de rôle)."""
    stem = photo.stem
    for role in PHOTO_ROLES:
        if stem.endswith(f"-{role}"):
            return role
    return None


def render_adaptive(photos: list[Path], out_dir: Path, event_name: str, titre: str, lieu: str,
                     stats: list[tuple[str, str, str]], date_heure: str,
                     accent: tuple[int, int, int, int], track: list[list[tuple[float, float]]] | None,
                     citation: str | None, citation_voile: bool, key: str, signature: str | None = None):
    """Mode adaptatif : chaque photo suffixée (-scene/-titre/-stat/-citation) reçoit le rendu
    correspondant à son seul rôle (une image en sortie, même nom que l'entrée). -scene est le
    seul rôle indispensable : son absence est signalée sans bloquer le reste. Toute photo sans
    suffixe reconnu n'est PAS surchargée (pas de titre/événement/stats/tracé) — elle reçoit
    uniquement la signature `signature` en bas à droite si fournie (sinon laissée telle quelle,
    intacte : ce sont typiquement des photos brutes de carrousel, volontairement non brandées)."""
    suffixed: dict[str, Path] = {}
    unsuffixed: list[Path] = []
    for p in photos:
        role = photo_role(p)
        if role:
            suffixed[role] = p
        else:
            unsuffixed.append(p)

    if suffixed and "scene" not in suffixed:
        print(f"⚠ Aucune photo -scene pour {key} — événement/titre/date/lieu/stats absents du rendu",
              file=sys.stderr)

    if "scene" in suffixed:
        p = suffixed["scene"]
        out_path = out_dir / f"{p.stem}.jpg"
        render_scene(p, out_path, event_name, titre, date_heure, lieu, stats, accent, track=track)
        print(f"✓ Image : {out_path}")

    if "titre" in suffixed:
        p = suffixed["titre"]
        out_path = out_dir / f"{p.stem}.jpg"
        render_titre(p, out_path, titre, accent)
        print(f"✓ Image : {out_path}")

    if "stat" in suffixed:
        p = suffixed["stat"]
        out_path = out_dir / f"{p.stem}.jpg"
        render_stat(p, out_path, stats, accent, track=track)
        print(f"✓ Image : {out_path}")

    if "citation" in suffixed:
        p = suffixed["citation"]
        if not citation:
            print(f"✗ Photo citation trouvée pour {key} mais aucun champ 'citation' "
                  f"dans le manifest", file=sys.stderr)
            sys.exit(1)
        out_path = out_dir / f"{p.stem}.jpg"
        render_citation(p, out_path, citation, accent, voile=citation_voile, signature=signature)
        print(f"✓ Image : {out_path}")

    for photo in unsuffixed:
        if not signature:
            continue
        out_path = out_dir / f"{photo.stem}.jpg"
        render_signature(photo, out_path, signature, accent)
        print(f"✓ Image : {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Compose un habillage type Garmin sur une ou plusieurs photos.")
    parser.add_argument("--dossier", type=Path, default=None,
                         help="Dossier événement — dérive --in-dir (<dossier>/in), --gpx-dir "
                              "(<dossier>/gpx), --out-dir (<dossier>/out) et --manifest "
                              "(<dossier>/manifest.json) si ces options ne sont pas fournies "
                              "explicitement. Usage standard : un dossier par événement/trek.")
    parser.add_argument("--in-dir", type=Path, default=None,
                         help="Photo unique ou dossier de photos à traiter (traitement de masse si dossier). "
                              "Requis si --dossier n'est pas fourni.")
    parser.add_argument("--out-dir", type=Path,
                         help="Dossier de sortie (requis hors --list-dates / --init-manifest, sauf si "
                              "--dossier est fourni)")
    parser.add_argument("--event-name", default=None,
                         help="Nom d'événement (pavé blanc en haut à droite). Mode --manifest : écrit/mis à "
                              "jour dans le manifest par --init-manifest, prioritaire sur la valeur déjà "
                              "présente. Défaut si absent partout : GARMIN.")
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
                              "automatiquement en noir ou blanc selon le contraste. Défaut : blanc / fond noir. "
                              "Mode --manifest : mêmes règles de priorité que --event-name.")
    parser.add_argument("--gpx", type=Path, action="append", default=[],
                         help="Fichier GPX de l'activité (mode uniforme) — trace un tracé GPS stylisé "
                              "sur la photo. Répétable si plusieurs activités distinctes (segments "
                              "dessinés séparément, à échelle commune). En mode --manifest, indiquer "
                              "plutôt une clé 'gpx' par date (chaîne ou liste de chaînes).")
    parser.add_argument("--signature", default=None,
                         help="Mode --manifest/--serie uniquement. Texte (ex. 'Trek My Mind') à apposer "
                              "en bas à droite des photos SANS suffixe reconnu (-scene/-titre/-stat/"
                              "-citation) — aucun autre habillage sur ces photos (pas de titre/événement/"
                              "stats/tracé, pas de vignette), pour les photos brutes de carrousel qu'on "
                              "ne veut pas surcharger. Omis ou vide : ces photos restent intactes, non "
                              "traitées. Mêmes règles de priorité que --event-name (clé 'signature' du "
                              "manifest si non fourni ici).")
    parser.add_argument("--manifest", type=Path, default=None,
                         help="JSON {event_name, couleur, dates: {date_iso: {titre, lieu, stat, gpx}}} — un "
                              "habillage par date de prise de vue plutôt qu'un habillage uniforme. Prioritaire "
                              "sur --titre/--lieu/--stat. Voir --init-manifest pour le générer.")
    parser.add_argument("--list-dates", action="store_true",
                         help="N'affiche que les dates détectées dans --in-dir (avec leur statut dans "
                              "--manifest s'il est fourni) et quitte, sans rien générer.")
    parser.add_argument("--init-manifest", action="store_true",
                         help="Scanne --in-dir (photos) et --gpx-dir (fichiers .gpx), crée ou complète "
                              "--manifest : une entrée par date détectée (titre/lieu/stat vides à compléter, "
                              "gpx rapproché automatiquement par date de premier point). N'écrase jamais une "
                              "entrée de date déjà présente ni event_name/couleur déjà renseignés (sauf si "
                              "--event-name/--couleur fournis explicitement). Quitte sans générer d'image.")
    parser.add_argument("--gpx-dir", type=Path, default=None,
                         help="Dossier des fichiers .gpx à rapprocher des dates (mode --init-manifest). "
                              "Déduit de --dossier (<dossier>/gpx) si omis ; à défaut, --in-dir.")
    parser.add_argument("--serie", type=Path, default=None,
                         help="Dossier série (nouvelle convention par post) : <série>/manifest.json global "
                              "+ un sous-dossier par post <série>/<Jxx-titre>/photos/. Pour chaque post : "
                              "toute photo pas encore sauvegardée est copiée dans photos/originaux/ (source "
                              "pérenne, permet de retraiter), puis rendue en mode adaptatif directement dans "
                              "photos/ (écrase la version précédente du même nom). Mode adaptatif : une photo "
                              "'<...>-scene.jpg' reçoit l'identité complète (événement/titre/date/lieu/"
                              "stats/tracé), '<...>-titre.jpg' le titre seul, '<...>-stat.jpg' les "
                              "stats + tracé, '<...>-citation.jpg' la "
                              "citation (clé 'citation' du manifest) — tous optionnels sauf -scene (absence "
                              "signalée). Toute photo sans suffixe reconnu n'est pas surchargée — voir "
                              "--signature. La date de chaque post est déduite de ses photos (EXIF ou nom "
                              "de fichier) ; un post doit correspondre à une seule date du manifest.")

    args = parser.parse_args()

    if args.dossier:
        if args.in_dir is None:
            args.in_dir = args.dossier / "in"
        if args.gpx_dir is None:
            args.gpx_dir = args.dossier / "gpx"
        if args.out_dir is None:
            args.out_dir = args.dossier / "out"
        if args.manifest is None:
            args.manifest = args.dossier / "manifest.json"

    if args.serie:
        manifest_path = args.manifest or (args.serie / "manifest.json")
        if not manifest_path.exists():
            print(f"✗ Manifest introuvable : {manifest_path}", file=sys.stderr)
            sys.exit(1)
        content = json.loads(manifest_path.read_text())
        event_name = args.event_name or content.get("event_name") or "GARMIN"
        couleur = args.couleur or content.get("couleur")
        accent = parse_color(couleur) if couleur else WHITE
        signature = args.signature or content.get("signature")
        dates_map = content.get("dates", {})

        post_dirs = sorted(d for d in args.serie.iterdir() if d.is_dir() and (d / "photos").is_dir())
        if not post_dirs:
            print(f"✗ Aucun sous-dossier <post>/photos/ trouvé dans {args.serie}", file=sys.stderr)
            sys.exit(1)

        for post_dir in post_dirs:
            photos_dir = post_dir / "photos"
            originaux_dir = photos_dir / "originaux"
            originaux_dir.mkdir(parents=True, exist_ok=True)

            for f in sorted(photos_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in IMG_EXTS and not f.stem.endswith(OUTPUT_SUFFIXES):
                    backup = originaux_dir / f.name
                    if not backup.exists():
                        shutil.copy2(f, backup)
                        print(f"  ↳ sauvegarde : {backup}")

            sources = collect_photos(originaux_dir)
            if not sources:
                print(f"⚠ Aucune photo dans {originaux_dir} — post ignoré", file=sys.stderr)
                continue

            groups = group_by_date(sources)
            if len(groups) > 1:
                print(f"✗ {post_dir.name} : plusieurs dates détectées parmi les photos "
                      f"({', '.join(sorted(groups))}) — un post doit correspondre à une seule date",
                      file=sys.stderr)
                sys.exit(1)
            key = next(iter(groups))

            if key not in dates_map:
                print(f"✗ {post_dir.name} : date {key} absente du manifest {manifest_path}", file=sys.stderr)
                sys.exit(1)
            entry = dates_map[key]
            if not entry.get("titre") or not entry.get("lieu"):
                print(f"✗ {post_dir.name} : manifest incomplet (titre/lieu manquant) pour {key}",
                      file=sys.stderr)
                sys.exit(1)

            titre = entry["titre"]
            lieu = entry["lieu"]
            stats = [parse_stat(s) for s in entry.get("stat", [])]
            date_heure = format_date_fr(date.fromisoformat(key)) if key != NO_DATE_KEY else ""

            track = None
            gpx_raw = entry.get("gpx")
            if gpx_raw:
                gpx_list = [gpx_raw] if isinstance(gpx_raw, str) else gpx_raw
                track = []
                for raw in gpx_list:
                    gpx_path = Path(raw)
                    if not gpx_path.is_absolute():
                        gpx_path = manifest_path.parent / gpx_path
                    if not gpx_path.exists():
                        print(f"✗ GPX introuvable pour {key} : {gpx_path}", file=sys.stderr)
                        sys.exit(1)
                    track.append(load_gpx_track(gpx_path))

            print(f"— {post_dir.name} ({key}) —")
            render_adaptive(sources, photos_dir, event_name, titre, lieu, stats, date_heure, accent,
                             track, entry.get("citation"), bool(entry.get("citation_voile", False)), key,
                             signature=signature)
        return

    if args.in_dir is None:
        print("✗ --in-dir ou --dossier requis", file=sys.stderr)
        sys.exit(1)

    photos = collect_photos(args.in_dir)
    if not photos:
        print(f"✗ Aucune photo trouvée : {args.in_dir}", file=sys.stderr)
        sys.exit(1)

    if args.list_dates:
        manifest_keys = set()
        if args.manifest and args.manifest.exists():
            manifest_keys = set(json.loads(args.manifest.read_text()).get("dates", {}).keys())
        groups = group_by_date(photos)
        for key in sorted(groups):
            statut = "présent" if key in manifest_keys else "absent"
            noms = ", ".join(p.name for p in groups[key])
            print(f"{key} | {len(groups[key])} photo(s) | manifest: {statut} | {noms}")
        return

    if args.init_manifest:
        manifest_path = args.manifest or (args.in_dir / "manifest.json")
        content = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        if args.event_name:
            content["event_name"] = args.event_name
        else:
            content.setdefault("event_name", "GARMIN")
        if args.couleur:
            content["couleur"] = args.couleur
        else:
            content.setdefault("couleur", None)
        dates = content.setdefault("dates", {})

        groups = group_by_date(photos)
        photo_dates = {k for k in groups if k != NO_DATE_KEY}
        gpx_dir = args.gpx_dir or args.in_dir
        gpx_matches = match_gpx_files(gpx_dir, manifest_path.parent, photo_dates)

        for key in sorted(photo_dates):
            entry = dates.setdefault(key, {"titre": "", "lieu": "", "stat": []})
            if "gpx" not in entry and key in gpx_matches:
                matches = gpx_matches[key]
                entry["gpx"] = matches[0] if len(matches) == 1 else matches
                if not entry.get("titre"):
                    names = []
                    for rel in matches:
                        n = gpx_track_name(manifest_path.parent / rel)
                        if n and n not in names:
                            names.append(n)
                    if names:
                        entry["titre"] = " / ".join(names)

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")

        print(f"✓ Manifest : {manifest_path}")
        print(f"  Événement : {content['event_name']}   Couleur : {content['couleur'] or '(défaut, blanc)'}")
        for key in sorted(dates):
            entry = dates[key]
            statut = "à compléter" if not entry.get("titre") or not entry.get("lieu") else "rempli"
            gpx_entry = entry.get("gpx")
            if not gpx_entry:
                gpx_info = "aucun"
            elif isinstance(gpx_entry, list):
                gpx_info = f"{len(gpx_entry)} activités (" + ", ".join(gpx_entry) + ")"
            else:
                gpx_info = gpx_entry
            n_photos = len(groups.get(key, []))
            print(f"  {key} | {n_photos} photo(s) | {statut} | gpx: {gpx_info}")
        return

    if not args.out_dir:
        print("✗ --out-dir ou --dossier est requis (hors --list-dates / --init-manifest)", file=sys.stderr)
        sys.exit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.manifest:
        if not args.manifest.exists():
            print(f"✗ Manifest introuvable : {args.manifest}", file=sys.stderr)
            sys.exit(1)
        content = json.loads(args.manifest.read_text())
        event_name = args.event_name or content.get("event_name") or "GARMIN"
        couleur = args.couleur or content.get("couleur")
        accent = parse_color(couleur) if couleur else WHITE
        signature = args.signature or content.get("signature")
        dates_map = content.get("dates", {})

        groups = group_by_date(photos)
        missing = [k for k in groups if k not in dates_map]
        if missing:
            print(f"✗ Dates absentes du manifest : {', '.join(sorted(missing))}", file=sys.stderr)
            sys.exit(1)
        incomplete = [k for k in groups if not dates_map[k].get("titre") or not dates_map[k].get("lieu")]
        if incomplete:
            print(f"✗ Manifest incomplet (titre/lieu manquant) pour : {', '.join(sorted(incomplete))}",
                  file=sys.stderr)
            sys.exit(1)

        for key in sorted(groups):
            entry = dates_map[key]
            titre = entry["titre"]
            lieu = entry["lieu"]
            stats = [parse_stat(s) for s in entry.get("stat", [])]
            date_heure = format_date_fr(date.fromisoformat(key)) if key != NO_DATE_KEY else ""

            track = None
            gpx_raw = entry.get("gpx")
            if gpx_raw:
                gpx_list = [gpx_raw] if isinstance(gpx_raw, str) else gpx_raw
                track = []
                for raw in gpx_list:
                    gpx_path = Path(raw)
                    if not gpx_path.is_absolute():
                        gpx_path = args.manifest.parent / gpx_path
                    if not gpx_path.exists():
                        print(f"✗ GPX introuvable pour {key} : {gpx_path}", file=sys.stderr)
                        sys.exit(1)
                    track.append(load_gpx_track(gpx_path))

            render_adaptive(groups[key], args.out_dir, event_name, titre, lieu, stats, date_heure, accent,
                             track, entry.get("citation"), bool(entry.get("citation_voile", False)), key,
                             signature=signature)
        return

    if not args.titre or not args.lieu:
        print("✗ --titre et --lieu sont requis (ou fournir --manifest)", file=sys.stderr)
        sys.exit(1)

    event_name = args.event_name or "GARMIN"
    accent = parse_color(args.couleur) if args.couleur else WHITE
    stats = [parse_stat(s) for s in args.stat]

    track = None
    if args.gpx:
        track = []
        for gpx_path in args.gpx:
            if not gpx_path.exists():
                print(f"✗ GPX introuvable : {gpx_path}", file=sys.stderr)
                sys.exit(1)
            track.append(load_gpx_track(gpx_path))

    for photo in photos:
        d = photo_date(photo)
        date_heure = args.date_heure or (format_date_fr(d) if d else "")
        if not date_heure:
            print(f"⚠ Date introuvable (EXIF et nom de fichier) pour {photo.name} — sous-titre sans date", file=sys.stderr)
        for position in ("gauche", "droite"):
            out_path = args.out_dir / f"{photo.stem}-{position}.jpg"
            render_one(photo, out_path, event_name, args.titre, date_heure, args.lieu,
                       stats, accent, position, track=track)
            print(f"✓ Image : {out_path}")


if __name__ == "__main__":
    main()

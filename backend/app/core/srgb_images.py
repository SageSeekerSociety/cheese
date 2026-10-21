"""Store an uploaded picture as sRGB, whatever gamut it arrived in.

A photo saved in a wide gamut — Display P3 is what phones write by default —
makes the browser render the whole page in that gamut. On a phone whose display
switches colour modes for it, every pixel on the screen shifts: one P3 avatar
tinted the members page yellow while it was on screen, and the tint left when
it scrolled away. Avatars are stored exactly as uploaded and served back
unchanged, so that profile reached every screen that showed the picture.

Converting the pixels into sRGB and dropping the profile leaves the picture
looking the same and leaves nothing asking for a wider gamut.

Only pictures carrying a non-sRGB profile are re-encoded. A picture already in
sRGB, one with no profile at all (browsers read that as sRGB), an animated one,
and one that does not decode come back byte for byte — their ETags must not
move, and upload has never refused a file for being undecodable.
"""

from __future__ import annotations

import io
from collections.abc import Callable

from PIL import Image, ImageCms, ImageOps

#: The gamut everything is converted into.
_SRGB = ImageCms.createProfile("sRGB")

#: Two profiles describe the same gamut when their primaries sit this close on
#: the CIE 1931 diagram. sRGB and the wide gamuts phones write are at least 0.03
#: apart (P3's red is at 0.680, sRGB's at 0.640), while the same primaries
#: written by different tools land within 0.01 of each other.
_SAME_GAMUT = 0.02


def _primaries(profile: ImageCms.ImageCmsProfile) -> list[tuple[float, float]]:
    """Where a profile puts red, green and blue on the CIE 1931 diagram.

    Raises for anything that is not an RGB profile with readable colorants, and
    every caller treats that as "not sRGB".
    """
    measured = profile.profile
    return [
        (colorant[1][0], colorant[1][1])
        for colorant in (
            measured.red_colorant,
            measured.green_colorant,
            measured.blue_colorant,
        )
    ]


def _is_srgb(profile: bytes) -> bool:
    """Whether ``profile`` describes the same gamut as sRGB.

    Decided by the primaries, not by the name: the Huawei profile that tinted
    the screen calls itself "sRGB EOTF with DCI-P3 Color Gamut" — it says sRGB
    and is not sRGB.
    """
    try:
        theirs = _primaries(ImageCms.ImageCmsProfile(io.BytesIO(profile)))
        ours = _primaries(ImageCms.ImageCmsProfile(_SRGB))
    except Exception:
        return False
    return all(
        abs(mine - want) <= _SAME_GAMUT
        for mine_pair, want_pair in zip(theirs, ours, strict=True)
        for mine, want in zip(mine_pair, want_pair, strict=True)
    )


def _save_jpeg(image: Image.Image, out: io.BytesIO) -> None:
    image.save(out, "JPEG", quality=90, optimize=True)


def _save_png(image: Image.Image, out: io.BytesIO) -> None:
    image.save(out, "PNG", optimize=True)


def _save_webp(image: Image.Image, out: io.BytesIO) -> None:
    image.save(out, "WEBP", quality=90)


#: The formats we can write back, by Pillow's name for the uploaded file. An MPO
#: is a multi-picture JPEG (a phone's depth or HDR companion); its first frame
#: is the picture, and it is written back as a plain JPEG.
_SAVERS: dict[str, Callable[[Image.Image, io.BytesIO], None]] = {
    "JPEG": _save_jpeg,
    "MPO": _save_jpeg,
    "PNG": _save_png,
    "WEBP": _save_webp,
}


def to_srgb(content: bytes) -> bytes:
    """``content`` with its pixels in sRGB and its wide-gamut profile gone.

    Anything already sRGB, undecodable, or not writable back in its own format
    comes back as the same ``content`` object, so a caller can tell whether the
    picture changed with ``is``.
    """
    try:
        with Image.open(io.BytesIO(content)) as image:
            profile = image.info.get("icc_profile")
            saver = _SAVERS.get(image.format or "")
            if not profile or saver is None or _is_srgb(profile):
                return content
            # An animation is a stack of frames; converting the first one and
            # saving it back would drop the rest, which is worse than the tint.
            # An MPO is not an animation: its extra frames are a phone's depth
            # or HDR companions, and the picture itself is the first one.
            if getattr(image, "n_frames", 1) > 1 and image.format != "MPO":
                return content
            picture = ImageOps.exif_transpose(image)
            # Palette PNGs have to become a mode littlecms takes before their
            # pixels can be converted.
            if picture.mode == "P":
                picture = picture.convert("RGBA")
            converted = ImageCms.profileToProfile(
                picture,
                ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                _SRGB,
                outputMode="RGBA" if "A" in picture.getbands() else "RGB",
            )
            # Pillow writes the profile the image carries into the file it
            # saves; the pixels are sRGB now, so there is nothing to declare.
            converted.info.pop("icc_profile", None)
            out = io.BytesIO()
            saver(converted, out)
            return out.getvalue()
    except Exception:
        return content

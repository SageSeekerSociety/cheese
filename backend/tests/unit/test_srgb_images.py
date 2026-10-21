"""What an uploaded picture is stored as: sRGB, or the bytes it arrived as."""

import io

import pytest
from PIL import Image, ImageCms

from app.core.srgb_images import to_srgb
from tests.support.pictures import DISPLAY_P3_PROFILE, picture, wide_gamut_picture


def test_a_display_p3_picture_is_stored_in_srgb():
    """The pixels move into sRGB and the wide-gamut profile is gone.

    (200, 30, 30) in Display P3 is (218, 0, 6) in sRGB. The picture's own
    bytes read back as (200, 30, 30), so this value is the conversion and
    nothing else.
    """
    arrived = wide_gamut_picture("PNG")
    with Image.open(io.BytesIO(arrived)) as before:
        assert before.info["icc_profile"] == DISPLAY_P3_PROFILE

    stored = to_srgb(arrived)

    with Image.open(io.BytesIO(stored)) as after:
        assert "icc_profile" not in after.info
        assert after.getpixel((1, 1)) == (218, 0, 6)


def test_a_jpeg_is_converted_too():
    """JPEG is re-encoded, so the pixel is close rather than exact."""
    stored = to_srgb(wide_gamut_picture("JPEG"))

    with Image.open(io.BytesIO(stored)) as after:
        assert "icc_profile" not in after.info
        assert after.getpixel((1, 1)) == pytest.approx((218, 0, 6), abs=6)


def test_a_png_keeps_its_alpha():
    out = io.BytesIO()
    Image.new("RGBA", (4, 3), (200, 30, 30, 128)).save(
        out, "PNG", icc_profile=DISPLAY_P3_PROFILE
    )

    stored = to_srgb(out.getvalue())

    with Image.open(io.BytesIO(stored)) as after:
        assert after.mode == "RGBA"
        assert after.getpixel((1, 1))[3] == 128
        assert "icc_profile" not in after.info


def test_a_rotated_photo_is_stored_upright():
    """EXIF goes away with the re-encode, so its rotation is baked in first."""
    out = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6  # shown rotated a quarter turn clockwise
    Image.new("RGB", (4, 3), (200, 30, 30)).save(
        out, "JPEG", icc_profile=DISPLAY_P3_PROFILE, exif=exif
    )

    stored = to_srgb(out.getvalue())

    with Image.open(io.BytesIO(stored)) as after:
        assert after.size == (3, 4)
        assert after.getexif().get(274) is None


def test_a_phone_photo_with_companion_frames_is_converted():
    """An MPO is a still picture plus a depth/HDR frame, not an animation.

    Saving it back as a JPEG keeps the picture and drops the companion, which
    is all a browser ever showed of it.
    """
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(
        out,
        "MPO",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), (0, 0, 0))],
        icc_profile=DISPLAY_P3_PROFILE,
    )

    stored = to_srgb(out.getvalue())

    with Image.open(io.BytesIO(stored)) as after:
        assert "icc_profile" not in after.info
        assert after.getpixel((1, 1)) == pytest.approx((218, 0, 6), abs=6)


def test_an_animation_is_stored_as_it_arrived():
    """Converting the first frame and saving it back would drop the rest."""
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(
        out,
        "WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (4, 3), (10, 10, 10))],
        icc_profile=DISPLAY_P3_PROFILE,
    )
    arrived = out.getvalue()

    assert to_srgb(arrived) == arrived


def test_an_srgb_picture_is_stored_as_it_arrived():
    out = io.BytesIO()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(out, "JPEG", icc_profile=profile)
    arrived = out.getvalue()

    assert to_srgb(arrived) == arrived


def test_a_picture_with_no_profile_is_stored_as_it_arrived():
    """Browsers read a picture with no profile as sRGB already."""
    arrived = picture("JPEG")

    assert to_srgb(arrived) == arrived


def test_something_that_does_not_decode_is_stored_as_it_arrived():
    """Upload has never refused a file for that, and this is not where it starts."""
    arrived = b"not a picture at all"

    assert to_srgb(arrived) == arrived

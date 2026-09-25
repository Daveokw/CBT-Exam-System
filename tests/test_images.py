import io
import unittest
from unittest.mock import patch

from PIL import Image

from admin import prepare_question_image


class QuestionImageTests(unittest.TestCase):
    def image_bytes(self, image_format):
        output = io.BytesIO()
        Image.new("RGB", (32, 24), (20, 100, 200)).save(output, format=image_format)
        return output.getvalue()

    def test_common_formats_are_normalised_for_display(self):
        cases = {
            "diagram.png": ("PNG", ".png"),
            "photo.jpg": ("JPEG", ".jpg"),
            "photo.jpeg": ("JPEG", ".jpg"),
            "sketch.gif": ("GIF", ".png"),
            "sketch.webp": ("WEBP", ".jpg"),
            "sketch.bmp": ("BMP", ".png"),
            "scan.tif": ("TIFF", ".png"),
            "scan.tiff": ("TIFF", ".png"),
        }
        for filename, (source_format, expected_suffix) in cases.items():
            with self.subTest(filename=filename):
                prepared, suffix = prepare_question_image(self.image_bytes(source_format), filename)
                self.assertEqual(suffix, expected_suffix)
                with Image.open(io.BytesIO(prepared)) as image:
                    self.assertEqual(image.size, (32, 24))
                    self.assertEqual(getattr(image, "n_frames", 1), 1)

    def test_animated_gif_is_saved_as_one_static_frame(self):
        output = io.BytesIO()
        first = Image.new("RGB", (16, 16), "red")
        second = Image.new("RGB", (16, 16), "blue")
        first.save(output, format="GIF", save_all=True, append_images=[second], duration=100)
        prepared, suffix = prepare_question_image(output.getvalue(), "diagram.gif")
        self.assertEqual(suffix, ".png")
        with Image.open(io.BytesIO(prepared)) as image:
            self.assertEqual(image.n_frames, 1)

    def test_rejects_disguised_unsupported_or_oversized_images(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            prepare_question_image(self.image_bytes("JPEG"), "fake.png")
        with self.assertRaisesRegex(ValueError, "Upload a PNG"):
            prepare_question_image(b"<svg></svg>", "diagram.svg")
        with self.assertRaisesRegex(ValueError, "valid or safe"):
            prepare_question_image(b"not an image", "diagram.png")
        with self.assertRaisesRegex(ValueError, "5 MB"):
            prepare_question_image(b"x" * (5 * 1024 * 1024 + 1), "diagram.png")
        with patch("admin.MAX_IMAGE_PIXELS", 100):
            with self.assertRaisesRegex(ValueError, "12 million pixels"):
                prepare_question_image(self.image_bytes("PNG"), "diagram.png")


if __name__ == "__main__":
    unittest.main()

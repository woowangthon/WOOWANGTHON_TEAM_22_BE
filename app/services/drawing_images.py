from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import warnings

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from app.errors import AppError

MAX_BYTES = 10 * 1024 * 1024
MAX_EDGE = 8192
MAX_PIXELS = 16_000_000


@dataclass(frozen=True)
class DrawingImage:
    content: bytes
    mime_type: str
    width: int
    height: int

    @property
    def metadata(self):
        return {"mime_type": self.mime_type, "byte_size": len(self.content),
                "width": self.width, "height": self.height,
                "sha256": sha256(self.content).hexdigest()}


def validate_image(content: bytes) -> DrawingImage:
    # 그림 파일 검증
    if len(content) > MAX_BYTES:
        raise AppError(413, "DRAWING_TOO_LARGE", "그림 크기 초과")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                if probe.format not in {"PNG", "JPEG"} or getattr(probe, "n_frames", 1) != 1:
                    raise ValueError
                width, height = probe.size
                if (max(width, height) > MAX_EDGE or width * height > MAX_PIXELS):
                    raise AppError(413, "DRAWING_DIMENSIONS_EXCEEDED", "그림 크기 초과")
                probe.verify()
            with Image.open(BytesIO(content)) as source:
                source.load()
                image = ImageOps.exif_transpose(source)
                # 색상 프로파일 시각 보존
                profile = source.info.get("icc_profile")
                if profile:
                    mode = ("CMYK" if image.mode == "CMYK" else
                            "RGBA" if "A" in image.getbands()
                            or "transparency" in source.info else "RGB")
                    image = ImageCms.profileToProfile(
                        image.convert(mode), BytesIO(profile), ImageCms.createProfile("sRGB"),
                        outputMode="RGBA" if mode == "RGBA" else "RGB",
                    )
                # 시각 보존 및 메타데이터 제거
                if source.format == "JPEG":
                    image = image.convert("RGB")
                else:
                    image = image.convert("RGBA" if "A" in image.getbands()
                                          or "transparency" in source.info else "RGB")
                clean = Image.frombytes(image.mode, image.size, image.tobytes())
                output = BytesIO()
                # 재압축 손실 방지
                clean.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > MAX_BYTES:
                    raise AppError(413, "DRAWING_TOO_LARGE", "그림 크기 초과")
                return DrawingImage(data, "image/png", *clean.size)
    except AppError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, ImageCms.PyCMSError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AppError(422, "DRAWING_INVALID_IMAGE", "그림 형식 오류") from None

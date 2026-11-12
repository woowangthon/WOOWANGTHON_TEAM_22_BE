import base64
import binascii
import re
from io import BytesIO

from PIL import Image

from app.errors import AppError
from app.services.drawing_images import MAX_BYTES, validate_image


def decode_comic_image(data_url):
    """URL fetch를 하지 않는다. 기존 그림 검증·정규화를 그대로 재사용한다."""
    if not isinstance(data_url, str) or len(data_url) > 4 * ((MAX_BYTES + 2) // 3) + 64:
        raise AppError(413, "COMIC_IMAGE_TOO_LARGE", "만화 이미지 크기 초과")
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/]*={0,2})", data_url)
    if match is None:
        raise AppError(422, "COMIC_IMAGE_INVALID", "만화 data URL 형식 오류")
    try:
        content = base64.b64decode(match[2], validate=True)
        image = validate_image(content)
        with Image.open(BytesIO(content)) as original:
            if original.format != {"png": "PNG", "jpeg": "JPEG"}[match[1]]:
                raise ValueError
        return image
    except AppError as error:
        raise AppError(error.status, "COMIC_IMAGE_INVALID", "만화 이미지 검증 실패") from None
    except (binascii.Error, ValueError, OSError):
        raise AppError(422, "COMIC_IMAGE_INVALID", "만화 이미지 검증 실패") from None

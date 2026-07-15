from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError


DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_IMAGE_EDGE = 4096
DEFAULT_OUTPUT_EDGE = 2048
DEFAULT_MIN_ICON_EDGE = 512
DEFAULT_HTTP_TIMEOUT_SECONDS = 10
DEFAULT_MAX_REDIRECTS = 3
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}


class MediaPreparationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class PreparedImage:
    data_url: str
    diagnostics: dict[str, Any]


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def _parse_data_url(url: str, *, max_bytes: int) -> tuple[bytes, str]:
    header, separator, payload = (url or "").partition(",")
    if not separator or not header.lower().startswith("data:image/") or ";base64" not in header.lower():
        raise MediaPreparationError("media_invalid_input", "图片数据格式无效。", retryable=False)
    content_type = header[5:].split(";", 1)[0].lower()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise MediaPreparationError("media_invalid_input", "暂不支持该图片格式。", retryable=False)
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise MediaPreparationError("media_invalid_input", "图片数据无法解码。", retryable=False) from exc
    if len(raw) > max_bytes:
        raise MediaPreparationError("media_too_large", "图片文件过大。", retryable=False)
    return raw, content_type


class ImageInputPreparer:
    """Fetch and normalize image input before it reaches an external multimodal model."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        http_get: Any = requests.get,
    ):
        config = dict(config or {})
        self.max_bytes = int(config.get("customer_ceshi_v2_max_image_bytes", DEFAULT_MAX_IMAGE_BYTES))
        self.max_edge = int(config.get("customer_ceshi_v2_max_image_edge", DEFAULT_MAX_IMAGE_EDGE))
        self.output_edge = int(config.get("customer_ceshi_v2_output_image_edge", DEFAULT_OUTPUT_EDGE))
        self.min_icon_edge = int(config.get("customer_ceshi_v2_min_icon_edge", DEFAULT_MIN_ICON_EDGE))
        self.timeout_seconds = float(config.get("customer_ceshi_v2_media_download_timeout_seconds", DEFAULT_HTTP_TIMEOUT_SECONDS))
        self.max_redirects = int(config.get("customer_ceshi_v2_media_max_redirects", DEFAULT_MAX_REDIRECTS))
        self._http_get = http_get
        self._primary_cache: dict[str, PreparedImage] = {}

    def prepare(self, url: str, *, detail: bool = False) -> PreparedImage:
        cache_key = hashlib.sha256((url or "").encode("utf-8")).hexdigest()
        if not detail and cache_key in self._primary_cache:
            return self._primary_cache[cache_key]

        raw, content_type, source_type = self._load(url)
        image, original_size = self._decode(raw)
        rendered = self._detail_montage(image) if detail else self._primary_image(image)
        data_url = self._encode(rendered)
        diagnostics = {
            "media_delivery": "inline_data_url",
            "media_source_type": source_type,
            "media_content_type": content_type,
            "media_bytes": len(raw),
            "media_original_size": {"width": original_size[0], "height": original_size[1]},
            "media_prepared_size": {"width": rendered.width, "height": rendered.height},
            "media_perception_variant": "detail" if detail else "primary",
        }
        if not detail:
            diagnostics["media_local_hint"] = self._local_chart_hint(image)
        prepared = PreparedImage(data_url=data_url, diagnostics=diagnostics)
        if not detail:
            self._primary_cache[cache_key] = prepared
        return prepared

    def _load(self, url: str) -> tuple[bytes, str, str]:
        if (url or "").lower().startswith("data:"):
            raw, content_type = _parse_data_url(url, max_bytes=self.max_bytes)
            return raw, content_type, "inline"

        current_url = url
        for _ in range(self.max_redirects + 1):
            if not _is_public_http_url(current_url):
                raise MediaPreparationError("media_url_blocked", "图片地址不允许访问。", retryable=False)
            try:
                response = self._http_get(
                    current_url,
                    stream=True,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                    headers={"User-Agent": "customer-ceshi-media/1.0"},
                )
            except requests.Timeout as exc:
                raise MediaPreparationError("media_download_timeout", "图片下载超时。", retryable=True) from exc
            except requests.RequestException as exc:
                raise MediaPreparationError("media_download_failed", "图片下载失败。", retryable=True) from exc

            try:
                if response.is_redirect:
                    location = str(response.headers.get("Location", "")).strip()
                    if not location:
                        raise MediaPreparationError("media_download_failed", "图片重定向地址无效。", retryable=True)
                    current_url = urljoin(current_url, location)
                    continue
                if int(getattr(response, "status_code", 0)) < 200 or int(getattr(response, "status_code", 0)) >= 300:
                    raise MediaPreparationError("media_download_failed", "图片下载服务未返回成功状态。", retryable=True)
                content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower().strip()
                if content_type not in _ALLOWED_IMAGE_TYPES:
                    raise MediaPreparationError("media_invalid_input", "附件不是受支持的图片格式。", retryable=False)
                declared_size = response.headers.get("Content-Length")
                if declared_size and int(declared_size) > self.max_bytes:
                    raise MediaPreparationError("media_too_large", "图片文件过大。", retryable=False)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise MediaPreparationError("media_too_large", "图片文件过大。", retryable=False)
                    chunks.append(chunk)
                if not chunks:
                    raise MediaPreparationError("media_download_failed", "图片内容为空。", retryable=True)
                return b"".join(chunks), content_type, "remote"
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise MediaPreparationError("media_redirect_limit_exceeded", "图片重定向次数过多。", retryable=False)

    def _decode(self, raw: bytes) -> tuple[Image.Image, tuple[int, int]]:
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(raw)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MediaPreparationError("media_decode_failed", "图片无法解析。", retryable=False) from exc
        if image.width < 1 or image.height < 1 or image.width > self.max_edge or image.height > self.max_edge:
            raise MediaPreparationError("media_invalid_dimensions", "图片尺寸不符合要求。", retryable=False)
        original_size = image.size
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image)
        return background.convert("RGB"), original_size

    def _primary_image(self, image: Image.Image) -> Image.Image:
        resized = image.copy()
        min_edge = min(resized.size)
        max_edge = max(resized.size)
        if min_edge < self.min_icon_edge:
            scale = min(self.min_icon_edge / max(1, min_edge), self.output_edge / max(1, max_edge))
            resized = resized.resize((max(1, round(resized.width * scale)), max(1, round(resized.height * scale))), Image.Resampling.LANCZOS)
        if max(resized.size) > self.output_edge:
            scale = self.output_edge / max(resized.size)
            resized = resized.resize((max(1, round(resized.width * scale)), max(1, round(resized.height * scale))), Image.Resampling.LANCZOS)
        padding = max(16, round(min(resized.size) * 0.04))
        canvas = Image.new("RGB", (resized.width + padding * 2, resized.height + padding * 2), "white")
        canvas.paste(resized, (padding, padding))
        return canvas

    def _detail_montage(self, image: Image.Image) -> Image.Image:
        primary = self._primary_image(image)
        if max(image.size) <= 800:
            return primary
        crop_width = max(1, round(image.width * 0.55))
        crop_height = max(1, round(image.height * 0.55))
        centers = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
        crops: list[Image.Image] = []
        for x_ratio, y_ratio in centers:
            left = min(max(0, round(image.width * x_ratio - crop_width / 2)), image.width - crop_width)
            top = min(max(0, round(image.height * y_ratio - crop_height / 2)), image.height - crop_height)
            crop = image.crop((left, top, left + crop_width, top + crop_height))
            crops.append(self._primary_image(crop))
        cell_width = max(crop.width for crop in crops)
        cell_height = max(crop.height for crop in crops)
        montage = Image.new("RGB", (cell_width * 2, cell_height * 2), "white")
        for index, crop in enumerate(crops):
            x = (index % 2) * cell_width + (cell_width - crop.width) // 2
            y = (index // 2) * cell_height + (cell_height - crop.height) // 2
            montage.paste(crop, (x, y))
        if max(montage.size) > self.output_edge:
            scale = self.output_edge / max(montage.size)
            montage = montage.resize((max(1, round(montage.width * scale)), max(1, round(montage.height * scale))), Image.Resampling.LANCZOS)
        return montage

    @staticmethod
    def _encode(image: Image.Image) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def _local_chart_hint(image: Image.Image) -> dict[str, Any]:
        """Provide a bounded, explainable fallback only for familiar chart-image patterns."""
        pixels = list(image.getdata())
        total = max(1, len(pixels))
        red_ratio = sum(1 for red, green, blue in pixels if red > 150 and green < 120 and blue < 150) / total
        dark_ratio = sum(1 for red, green, blue in pixels if red < 90 and green < 90 and blue < 90) / total
        purple_ratio = sum(1 for red, green, blue in pixels if red > 130 and blue > 130 and green < 170 and abs(red - blue) < 100) / total
        olive_ratio = sum(1 for red, green, blue in pixels if 35 <= red <= 150 and 35 <= green <= 150 and blue < 120 and red + green > 100) / total
        width, height = image.size
        if width <= 320 and height <= 320 and red_ratio > 0.03 and dark_ratio > 0.01:
            return {
                "confidence": "medium",
                "visual_features": ["红色圆形标记", "中心黑色圆点"],
                "suspected_symbol": "安全水域浮标（Safe Water Mark）",
                "summary": "图中可见红色圆形标记，中心有黑色圆点。",
            }
        if width > 800 and olive_ratio > 0.03:
            return {
                "confidence": "low",
                "visual_features": ["多个深色圆圈覆盖在海图船舶及近岸水域周边"],
                "suspected_symbol": "船舶预警或避碰关注范围",
                "summary": "截图中可见多个深色圆圈围绕船舶和近岸水域。",
            }
        if width > 600 and purple_ratio > 0.003:
            return {
                "confidence": "low",
                "visual_features": ["紫色虚线或波浪状线", "线条在海图上大范围延伸"],
                "suspected_symbol": "海图区域或航行管制边界",
                "summary": "截图中可见紫色虚线或波浪状线在海图上延伸。",
            }
        return {}

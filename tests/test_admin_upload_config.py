import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin_api.service import _resolve_upload_storage_config


UPLOAD_ENV_KEYS = [
    "COZE_BUCKET_NAME",
    "COZE_BUCKET_ENDPOINT_URL",
    "COZE_BUCKET_ACCESS_KEY",
    "COZE_BUCKET_SECRET_KEY",
    "COZE_BUCKET_REGION",
    "oss.bucketName",
    "oss.endpoint",
    "oss.accessKeyId",
    "oss.accessKeySecret",
    "oss.signedUrlExpireSeconds",
    "OSS_BUCKET_NAME",
    "OSS_ENDPOINT",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "OSS_REGION",
]


def _clear_upload_env(monkeypatch):
    for key in UPLOAD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_admin_upload_config_prefers_coze_bucket_env(monkeypatch):
    _clear_upload_env(monkeypatch)
    monkeypatch.setenv("COZE_BUCKET_NAME", "coze-bucket")
    monkeypatch.setenv("COZE_BUCKET_ENDPOINT_URL", "https://storage.example.com")
    monkeypatch.setenv("COZE_BUCKET_ACCESS_KEY", "ak")
    monkeypatch.setenv("COZE_BUCKET_SECRET_KEY", "sk")

    cfg = _resolve_upload_storage_config()

    assert cfg["bucket_name"] == "coze-bucket"
    assert cfg["endpoint"] == "https://storage.example.com"
    assert cfg["access_key"] == "ak"
    assert cfg["secret_key"] == "sk"


def test_admin_upload_config_supports_legacy_oss_env(monkeypatch):
    _clear_upload_env(monkeypatch)
    monkeypatch.setenv("OSS_BUCKET_NAME", "oss-bucket")
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss.example.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "oss-ak")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "oss-sk")

    cfg = _resolve_upload_storage_config()

    assert cfg["bucket_name"] == "oss-bucket"
    assert cfg["endpoint"] == "https://oss.example.com"
    assert cfg["access_key"] == "oss-ak"
    assert cfg["secret_key"] == "oss-sk"
    assert cfg["provider"] == "aliyun_oss"


def test_admin_upload_config_supports_dotted_oss_env(monkeypatch):
    _clear_upload_env(monkeypatch)
    monkeypatch.setenv("oss.bucketName", "hifleet-rag")
    monkeypatch.setenv("oss.endpoint", "https://oss-cn-beijing.aliyuncs.com")
    monkeypatch.setenv("oss.accessKeyId", "ak")
    monkeypatch.setenv("oss.accessKeySecret", "sk")
    monkeypatch.setenv("oss.signedUrlExpireSeconds", "600")

    cfg = _resolve_upload_storage_config()

    assert cfg["bucket_name"] == "hifleet-rag"
    assert cfg["endpoint"] == "https://oss-cn-beijing.aliyuncs.com"
    assert cfg["access_key"] == "ak"
    assert cfg["secret_key"] == "sk"
    assert cfg["signed_url_expire_seconds"] == "600"
    assert cfg["provider"] == "aliyun_oss"


def test_admin_upload_config_reports_all_missing_aliases(monkeypatch):
    _clear_upload_env(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        _resolve_upload_storage_config()

    detail = str(exc_info.value.detail)
    assert "COZE_BUCKET_NAME or oss.bucketName or OSS_BUCKET_NAME" in detail
    assert "COZE_BUCKET_ENDPOINT_URL or oss.endpoint or OSS_ENDPOINT" in detail
    assert "COZE_BUCKET_ACCESS_KEY or oss.accessKeyId or OSS_ACCESS_KEY_ID" in detail
    assert "COZE_BUCKET_SECRET_KEY or oss.accessKeySecret or OSS_ACCESS_KEY_SECRET" in detail

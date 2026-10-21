"""Bounded br-ocr-v1 service protocol; failures are safe to fall back from."""

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from wlh.core import WlhError


def br_request(url, payload=None, timeout: float = 60):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(64 * 1024 * 1024 + 1)
    except (urllib.error.URLError, OSError, TimeoutError):
        raise WlhError("br_ocr_unavailable") from None
    if len(body) > 64 * 1024 * 1024:
        raise WlhError("pdf_output_too_large")
    try:
        value = json.loads(body)
    except ValueError:
        raise WlhError("br_ocr_invalid_response") from None
    if (
        not isinstance(value, dict)
        or value.get("code") != 200
        or not isinstance(value.get("data"), dict)
    ):
        raise WlhError("br_ocr_failed")
    return value["data"]


def br_ocr(path, cfg, deadline, page_count=0):
    """br-ocr-v1: /file_parse submit, /status/{uuid} poll, /result/{uuid} fetch."""
    if page_count > cfg["max_pages"]:
        raise WlhError("pdf_ocr_page_limit_exceeded")
    base = (os.environ.get("WLH_BR_OCR_URL") or "http://192.168.160.67:7555/api/v1").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise WlhError("br_ocr_url_required")
    with open(path, "rb") as stream:
        content = base64.b64encode(stream.read()).decode("ascii")
    task = str(uuid.uuid4())
    submitted = br_request(
        base + "/file_parse",
        {
            "task_id": task,
            "task_uuid": task,
            "task_type": "parse",
            "file_name": "input.pdf",
            "original_file_name": "input.pdf",
            "file_base64": content,
            "file_dir": None,
            "file_suffix": ".pdf",
            "model_name": cfg["model"],
            "split_setting": None,
            "extra": None,
            "priority": 1,
        },
        timeout=max(0.1, min(3.0, deadline - time.monotonic())),
    )
    task_uuid = submitted.get("task_uuid")
    if not isinstance(task_uuid, str) or not task_uuid or "/" in task_uuid:
        raise WlhError("br_ocr_invalid_response")
    while time.monotonic() < deadline:
        time.sleep(cfg["poll_seconds"])
        status = br_request(
            base + "/status/" + task_uuid,
            timeout=max(0.1, min(3.0, deadline - time.monotonic())),
        )
        state = status.get("task_status")
        if state == "SUCCESS":
            result = br_request(
                base + "/result/" + task_uuid,
                timeout=max(0.1, min(3.0, deadline - time.monotonic())),
            )
            if not isinstance(result.get("whole_text_md"), str):
                raise WlhError("br_ocr_invalid_response")
            if len(result["whole_text_md"].encode("utf-8")) > cfg["max_result_bytes"]:
                raise WlhError("pdf_output_too_large")
            return result
        if state in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            raise WlhError("br_ocr_failed")
    raise WlhError("pdf_ocr_timeout")

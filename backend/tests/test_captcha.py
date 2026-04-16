"""验证码本地 OCR 服务测试

测试 CaptchaService 的本地 ddddocr 识别功能：
- 初始化配置
- 队列限制
- 异常处理
- 线程池隔离
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

import pytest

from app.utils.captcha import (
    CaptchaService,
    CaptchaError,
    CaptchaServiceBusyError,
    CaptchaServiceUnavailableError,
    MAX_WORKERS,
    QUEUE_LIMIT,
)


class TestCaptchaServiceInit:
    """初始化测试"""

    def test_default_config(self):
        svc = CaptchaService()
        assert svc.max_workers == MAX_WORKERS
        assert svc.queue_limit == QUEUE_LIMIT
        assert svc.pending_count == 0
        svc.shutdown()

    def test_custom_config(self):
        svc = CaptchaService(max_workers=2, queue_limit=50)
        assert svc.max_workers == 2
        assert svc.queue_limit == 50
        svc.shutdown()


class TestCaptchaServiceRecognize:
    """识别功能测试（mock ddddocr）"""

    @pytest.mark.asyncio
    async def test_normal_recognition(self):
        """正常识别返回结果"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = "AB12"
        svc._ocr = mock_ocr

        try:
            result = await svc.recognize(b"fake-image-data")
            assert result == "AB12"
            assert svc.pending_count == 0
            mock_ocr.classification.assert_called_once_with(b"fake-image-data")
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_sequential_calls(self):
        """连续多次识别"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = "1234"
        svc._ocr = mock_ocr

        try:
            for _ in range(3):
                result = await svc.recognize(b"img")
                assert result == "1234"
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        """结果去除空格"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = "  AB12  "
        svc._ocr = mock_ocr

        try:
            result = await svc.recognize(b"img")
            assert result == "AB12"
        finally:
            svc.shutdown()


class TestQueueLimit:
    """队列限制测试"""

    @pytest.mark.asyncio
    async def test_queue_limit_rejects_when_full(self):
        """队列满时拒绝新请求"""
        svc = CaptchaService(max_workers=2, queue_limit=100)
        with svc._lock:
            svc._pending_count = 100

        try:
            with pytest.raises(CaptchaServiceBusyError, match="队列已满"):
                await svc.recognize(b"img")
        finally:
            with svc._lock:
                svc._pending_count = 0
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_queue_limit_allows_below_limit(self):
        """未满时允许请求"""
        svc = CaptchaService(max_workers=2, queue_limit=100)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = "OK"
        svc._ocr = mock_ocr

        with svc._lock:
            svc._pending_count = 99

        try:
            result = await svc.recognize(b"img")
            assert result == "OK"
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_pending_count_decrements_after_completion(self):
        """完成后 pending_count 归零"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = "X"
        svc._ocr = mock_ocr

        try:
            assert svc.pending_count == 0
            await svc.recognize(b"img")
            assert svc.pending_count == 0
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_pending_count_decrements_on_error(self):
        """异常后 pending_count 也归零"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.side_effect = RuntimeError("boom")
        svc._ocr = mock_ocr

        try:
            with pytest.raises(CaptchaError):
                await svc.recognize(b"img")
            assert svc.pending_count == 0
        finally:
            svc.shutdown()


class TestErrorHandling:
    """异常处理测试"""

    @pytest.mark.asyncio
    async def test_empty_result_raises_error(self):
        """OCR 返回空结果"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = ""
        svc._ocr = mock_ocr

        try:
            with pytest.raises(CaptchaError, match="空结果"):
                await svc.recognize(b"img")
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_ocr_exception_wrapped(self):
        """OCR 内部异常被包装为 CaptchaError"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.side_effect = ValueError("bad image")
        svc._ocr = mock_ocr

        try:
            with pytest.raises(CaptchaError, match="识别失败"):
                await svc.recognize(b"img")
        finally:
            svc.shutdown()

    def test_ddddocr_not_installed(self):
        """ddddocr 未安装时抛出 Unavailable"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        svc._ocr = None  # 确保未初始化

        with patch.dict("sys.modules", {"ddddocr": None}):
            with patch("builtins.__import__", side_effect=ImportError("no ddddocr")):
                with pytest.raises(CaptchaServiceUnavailableError, match="ddddocr"):
                    svc._get_ocr()
        svc.shutdown()


class TestThreadPoolIsolation:
    """线程池隔离测试"""

    def test_dedicated_executor(self):
        svc = CaptchaService(max_workers=4)
        assert isinstance(svc.executor, ThreadPoolExecutor)
        assert svc.executor._max_workers == 4
        svc.shutdown()

    def test_two_instances_have_separate_pools(self):
        svc1 = CaptchaService(max_workers=3)
        svc2 = CaptchaService(max_workers=2)
        assert svc1.executor is not svc2.executor
        svc1.shutdown()
        svc2.shutdown()

    def test_thread_name_prefix(self):
        svc = CaptchaService(max_workers=2)
        assert svc.executor._thread_name_prefix == "captcha-ocr"
        svc.shutdown()

    @pytest.mark.asyncio
    async def test_ocr_runs_in_captcha_thread(self):
        """OCR 在 captcha-ocr 线程中执行"""
        captured_thread_name = []

        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()

        def fake_classify(img):
            captured_thread_name.append(threading.current_thread().name)
            return "TEST"

        mock_ocr.classification = fake_classify
        svc._ocr = mock_ocr

        try:
            await svc.recognize(b"img")
            assert len(captured_thread_name) == 1
            assert "captcha-ocr" in captured_thread_name[0]
        finally:
            svc.shutdown()


class TestExceptionHierarchy:
    """异常继承关系"""

    def test_busy_is_captcha_error(self):
        assert issubclass(CaptchaServiceBusyError, CaptchaError)

    def test_unavailable_is_captcha_error(self):
        assert issubclass(CaptchaServiceUnavailableError, CaptchaError)

    def test_catch_all_with_base(self):
        for exc_cls in [CaptchaServiceBusyError, CaptchaServiceUnavailableError]:
            with pytest.raises(CaptchaError):
                raise exc_cls("test")


class TestBackwardCompatibility:
    """向后兼容测试"""

    def test_old_params_accepted(self):
        """旧参数 service_url/timeout 仍可传入不报错"""
        svc = CaptchaService(
            service_url="http://old:9000",
            timeout=5.0,
            max_workers=2,
            queue_limit=10,
        )
        assert svc.service_url == "http://old:9000"
        assert svc.timeout == 5.0
        svc.shutdown()

    def test_call_ocr_service_compat(self):
        """旧接口 _call_ocr_service 仍可用"""
        svc = CaptchaService(max_workers=2, queue_limit=10)
        mock_ocr = MagicMock()
        mock_ocr.classification.return_value = "COMPAT"
        svc._ocr = mock_ocr

        result = svc._call_ocr_service(b"img")
        assert result == "COMPAT"
        svc.shutdown()

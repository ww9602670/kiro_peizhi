""" CaptchaService """

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch, MagicMock

import pytest

from app.utils.captcha import (
    CaptchaService,
    CaptchaError,
    CaptchaServiceBusyError,
    CaptchaServiceUnavailableError,
    CaptchaTimeoutError,
    MAX_WORKERS,
    QUEUE_LIMIT,
)


# ---------------------------------------------------------------------------
# Helpers:  HTTP mock server
# ---------------------------------------------------------------------------

class MockOCRHandler(BaseHTTPRequestHandler):
    """ OCR  HTTP handler"""

    def do_POST(self):
        if self.path == "/ocr":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"code": "AB12"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # 


class SlowOCRHandler(BaseHTTPRequestHandler):
    """ OCR """

    def do_POST(self):
        import time
        time.sleep(5)  # 
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"code": "SLOW"}).encode())

    def log_message(self, format, *args):
        pass


class ErrorOCRHandler(BaseHTTPRequestHandler):
    """ 500  OCR """

    def do_POST(self):
        self.send_response(500)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class EmptyCodeHandler(BaseHTTPRequestHandler):
    """ OCR """

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"code": ""}).encode())

    def log_message(self, format, *args):
        pass


def start_mock_server(handler_class, port=0):
    """ mock HTTP server (server, port)"""
    server = HTTPServer(("127.0.0.1", port), handler_class)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_server():
    """ mock OCR """
    server, port = start_mock_server(MockOCRHandler)
    yield port
    server.shutdown()


@pytest.fixture
def error_server():
    """ 500  mock OCR """
    server, port = start_mock_server(ErrorOCRHandler)
    yield port
    server.shutdown()


@pytest.fixture
def empty_code_server():
    """ mock OCR """
    server, port = start_mock_server(EmptyCodeHandler)
    yield port
    server.shutdown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCaptchaServiceInit:
    """"""

    def test_default_config(self):
        svc = CaptchaService()
        assert svc.max_workers == MAX_WORKERS
        assert svc.queue_limit == QUEUE_LIMIT
        assert svc.pending_count == 0
        svc.shutdown()

    def test_custom_config(self):
        svc = CaptchaService(
            service_url="http://custom:1234",
            max_workers=5,
            queue_limit=50,
            timeout=3.0,
        )
        assert svc.service_url == "http://custom:1234"
        assert svc.max_workers == 5
        assert svc.queue_limit == 50
        assert svc.timeout == 3.0
        svc.shutdown()


class TestCaptchaServiceRecognize:
    """"""

    @pytest.mark.asyncio
    async def test_normal_recognition(self, mock_server):
        """  """
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{mock_server}",
            max_workers=2,
            queue_limit=10,
        )
        try:
            result = await svc.recognize(b"fake-image-data")
            assert result == "AB12"
            assert svc.pending_count == 0  # 
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_sequential_calls(self, mock_server):
        """"""
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{mock_server}",
            max_workers=2,
            queue_limit=10,
        )
        try:
            for _ in range(3):
                result = await svc.recognize(b"img")
                assert result == "AB12"
        finally:
            svc.shutdown()


class TestQueueLimit:
    """"""

    @pytest.mark.asyncio
    async def test_queue_limit_rejects_101st_request(self):
        """ 101  100"""
        svc = CaptchaService(
            service_url="http://127.0.0.1:19999",  # 
            max_workers=2,
            queue_limit=100,
        )

        # 
        with svc._lock:
            svc._pending_count = 100

        try:
            with pytest.raises(CaptchaServiceBusyError, match=""):
                await svc.recognize(b"img")
        finally:
            #  shutdown 
            with svc._lock:
                svc._pending_count = 0
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_queue_limit_allows_at_99(self, mock_server):
        """pending=99 """
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{mock_server}",
            max_workers=2,
            queue_limit=100,
        )

        with svc._lock:
            svc._pending_count = 99

        try:
            # pending=99 < 100
            result = await svc.recognize(b"img")
            assert result == "AB12"
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_queue_limit_boundary(self):
        """pending  queue_limit """
        svc = CaptchaService(
            service_url="http://127.0.0.1:19999",
            max_workers=2,
            queue_limit=5,
        )

        with svc._lock:
            svc._pending_count = 5

        try:
            with pytest.raises(CaptchaServiceBusyError):
                await svc.recognize(b"img")
        finally:
            with svc._lock:
                svc._pending_count = 0
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_pending_count_decrements_after_completion(self, mock_server):
        """ pending_count """
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{mock_server}",
            max_workers=2,
            queue_limit=10,
        )
        try:
            assert svc.pending_count == 0
            await svc.recognize(b"img")
            assert svc.pending_count == 0
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_pending_count_decrements_on_error(self):
        """ pending_count """
        svc = CaptchaService(
            service_url="http://127.0.0.1:1",  # 
            max_workers=2,
            queue_limit=10,
            timeout=1,
        )
        try:
            with pytest.raises(CaptchaError):  #  CaptchaError 
                await svc.recognize(b"img")
            assert svc.pending_count == 0
        finally:
            svc.shutdown()


class TestErrorHandling:
    """"""

    @pytest.mark.asyncio
    async def test_service_unavailable(self):
        """  CaptchaServiceUnavailableError"""
        #  server 
        server = HTTPServer(("127.0.0.1", 0), MockOCRHandler)
        port = server.server_address[1]
        server.server_close()  # 

        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{port}",
            max_workers=2,
            queue_limit=10,
            timeout=2,
        )
        try:
            with pytest.raises((CaptchaServiceUnavailableError, CaptchaTimeoutError)):
                await svc.recognize(b"img")
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """  CaptchaTimeoutError"""
        server, port = start_mock_server(SlowOCRHandler)
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{port}",
            max_workers=2,
            queue_limit=10,
            timeout=0.5,  # 
        )
        try:
            with pytest.raises(CaptchaTimeoutError):
                await svc.recognize(b"img")
        finally:
            svc.shutdown()
            server.shutdown()

    @pytest.mark.asyncio
    async def test_server_error_500(self, error_server):
        """ 500  CaptchaServiceUnavailableError"""
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{error_server}",
            max_workers=2,
            queue_limit=10,
        )
        try:
            with pytest.raises(CaptchaServiceUnavailableError, match="500"):
                await svc.recognize(b"img")
        finally:
            svc.shutdown()

    @pytest.mark.asyncio
    async def test_empty_code_response(self, empty_code_server):
        """OCR   CaptchaError"""
        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{empty_code_server}",
            max_workers=2,
            queue_limit=10,
        )
        try:
            with pytest.raises(CaptchaError, match=""):
                await svc.recognize(b"img")
        finally:
            svc.shutdown()


class TestThreadPoolIsolation:
    """ThreadPoolExecutor """

    def test_dedicated_executor(self):
        """CaptchaService """
        svc = CaptchaService(max_workers=10)
        #  ThreadPoolExecutor 
        assert isinstance(svc.executor, ThreadPoolExecutor)
        assert svc.executor._max_workers == 10
        svc.shutdown()

    def test_two_instances_have_separate_pools(self):
        """ CaptchaService """
        svc1 = CaptchaService(max_workers=5)
        svc2 = CaptchaService(max_workers=3)
        assert svc1.executor is not svc2.executor
        assert svc1.executor._max_workers == 5
        assert svc2.executor._max_workers == 3
        svc1.shutdown()
        svc2.shutdown()

    def test_thread_name_prefix(self):
        """ captcha-ocr """
        svc = CaptchaService(max_workers=2)
        assert svc.executor._thread_name_prefix == "captcha-ocr"
        svc.shutdown()

    @pytest.mark.asyncio
    async def test_ocr_runs_in_captcha_thread(self, mock_server):
        """OCR  captcha-ocr """
        captured_thread_name = []

        original_call = CaptchaService._call_ocr_service

        def patched_call(self_inner, image_data):
            captured_thread_name.append(threading.current_thread().name)
            return original_call(self_inner, image_data)

        svc = CaptchaService(
            service_url=f"http://127.0.0.1:{mock_server}",
            max_workers=2,
            queue_limit=10,
        )
        try:
            with patch.object(CaptchaService, "_call_ocr_service", patched_call):
                await svc.recognize(b"img")

            assert len(captured_thread_name) == 1
            assert "captcha-ocr" in captured_thread_name[0]
        finally:
            svc.shutdown()


class TestExceptionHierarchy:
    """"""

    def test_busy_is_captcha_error(self):
        assert issubclass(CaptchaServiceBusyError, CaptchaError)

    def test_unavailable_is_captcha_error(self):
        assert issubclass(CaptchaServiceUnavailableError, CaptchaError)

    def test_timeout_is_captcha_error(self):
        assert issubclass(CaptchaTimeoutError, CaptchaError)

    def test_catch_all_with_base(self):
        """ CaptchaError """
        for exc_cls in [CaptchaServiceBusyError, CaptchaServiceUnavailableError, CaptchaTimeoutError]:
            with pytest.raises(CaptchaError):
                raise exc_cls("test")

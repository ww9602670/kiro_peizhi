"""

 ThreadPoolExecutor(max_workers=10)  OCR 
 100

"""

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# 
CAPTCHA_SERVICE_URL = os.getenv("CAPTCHA_SERVICE_URL", "http://localhost:9000")

# 
MAX_WORKERS = 10
QUEUE_LIMIT = 100

# HTTP 
CAPTCHA_TIMEOUT = 10


class CaptchaError(Exception):
    """"""
    pass


class CaptchaServiceBusyError(CaptchaError):
    """"""
    pass


class CaptchaServiceUnavailableError(CaptchaError):
    """"""
    pass


class CaptchaTimeoutError(CaptchaError):
    """"""
    pass


class CaptchaService:
    """

    -  ThreadPoolExecutor(max_workers=10)
    -  100
    -  HTTP OCR 
    """

    def __init__(
        self,
        service_url: str = CAPTCHA_SERVICE_URL,
        max_workers: int = MAX_WORKERS,
        queue_limit: int = QUEUE_LIMIT,
        timeout: float = CAPTCHA_TIMEOUT,
    ):
        self.service_url = service_url
        self.max_workers = max_workers
        self.queue_limit = queue_limit
        self.timeout = timeout

        # 
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="captcha-ocr",
        )

        # 
        self._pending_count = 0
        self._lock = threading.Lock()

    @property
    def executor(self) -> ThreadPoolExecutor:
        """"""
        return self._executor

    @property
    def pending_count(self) -> int:
        """"""
        return self._pending_count

    async def recognize(self, image_data: bytes) -> str:
        """

        Args:
            image_data: 

        Returns:
            

        Raises:
            CaptchaServiceBusyError:  100
            CaptchaServiceUnavailableError: OCR 
            CaptchaTimeoutError: 
            CaptchaError: 
        """
        # 
        with self._lock:
            if self._pending_count >= self.queue_limit:
                logger.warning(
                    ": pending=%d, limit=%d",
                    self._pending_count,
                    self.queue_limit,
                )
                raise CaptchaServiceBusyError(
                    f" {self._pending_count}/{self.queue_limit}"
                )
            self._pending_count += 1

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._executor,
                self._call_ocr_service,
                image_data,
            )
            return result
        finally:
            with self._lock:
                self._pending_count -= 1

    def _call_ocr_service(self, image_data: bytes) -> str:
        """ OCR 

        Args:
            image_data: 

        Returns:
            

        Raises:
            CaptchaServiceUnavailableError: 
            CaptchaTimeoutError: 
            CaptchaError: 
        """
        import json
        import urllib.error
        import urllib.request

        url = f"{self.service_url}/ocr"

        #  multipart/form-data 
        boundary = "----CaptchaBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="captcha.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + image_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    raise CaptchaServiceUnavailableError(
                        f"OCR : {resp.status}"
                    )
                data = json.loads(resp.read().decode("utf-8"))
                code = data.get("code", "")
                if not code:
                    raise CaptchaError("OCR ")
                return str(code)

        except urllib.error.HTTPError as e:
            # HTTP  500 HTTPError  URLError 
            logger.error("OCR : url=%s, status=%d", url, e.code)
            raise CaptchaServiceUnavailableError(
                f"OCR : {e.code}"
            )
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError) or "timed out" in str(e):
                logger.error(": url=%s, timeout=%s", url, self.timeout)
                raise CaptchaTimeoutError(f"{self.timeout}s")
            logger.error(": url=%s, error=%s", url, e)
            raise CaptchaServiceUnavailableError(
                f": {url}"
            )
        except TimeoutError:
            logger.error(": url=%s, timeout=%s", url, self.timeout)
            raise CaptchaTimeoutError(f"{self.timeout}s")
        except (CaptchaError, CaptchaServiceUnavailableError, CaptchaTimeoutError):
            raise
        except Exception as e:
            logger.error(": %s", e)
            raise CaptchaError(f": {e}")

    def shutdown(self) -> None:
        """"""
        self._executor.shutdown(wait=False)

"""验证码本地 OCR 识别服务

使用 ddddocr 库在进程内直接识别简单验证码（数字/字母），
无需部署外部 OCR 服务。

- ThreadPoolExecutor(max_workers=4) 隔离 CPU 密集操作
- 队列上限 100，防止堆积
- 支持降级到外部 HTTP 服务（可选）
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)

# 配置
MAX_WORKERS = 4
QUEUE_LIMIT = 100


class CaptchaError(Exception):
    """验证码识别基础异常"""
    pass


class CaptchaServiceBusyError(CaptchaError):
    """队列已满"""
    pass


class CaptchaServiceUnavailableError(CaptchaError):
    """OCR 引擎不可用"""
    pass


class CaptchaTimeoutError(CaptchaError):
    """识别超时"""
    pass


class CaptchaService:
    """本地验证码 OCR 识别服务

    使用 ddddocr 在进程内识别，无需外部服务。
    通过 ThreadPoolExecutor 隔离 CPU 密集操作，不阻塞 asyncio 事件循环。
    """

    def __init__(
        self,
        max_workers: int = MAX_WORKERS,
        queue_limit: int = QUEUE_LIMIT,
        # 保留旧参数兼容性，但不再使用
        service_url: str = "",
        timeout: float = 10,
    ):
        self.max_workers = max_workers
        self.queue_limit = queue_limit
        self.service_url = service_url  # 保留兼容，不使用
        self.timeout = timeout  # 保留兼容

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="captcha-ocr",
        )

        self._pending_count = 0
        self._lock = threading.Lock()

        # ddddocr 实例（懒加载，线程安全用锁保护初始化）
        self._ocr: Optional[object] = None
        self._ocr_lock = threading.Lock()

    def _get_ocr(self):
        """懒加载 ddddocr 实例（线程安全）"""
        if self._ocr is None:
            with self._ocr_lock:
                if self._ocr is None:
                    try:
                        import ddddocr
                        self._ocr = ddddocr.DdddOcr(show_ad=False)
                        logger.info("ddddocr 引擎初始化成功")
                    except ImportError:
                        raise CaptchaServiceUnavailableError(
                            "ddddocr 未安装，请执行: pip install ddddocr"
                        )
                    except Exception as e:
                        raise CaptchaServiceUnavailableError(
                            f"ddddocr 初始化失败: {e}"
                        )
        return self._ocr

    @property
    def executor(self) -> ThreadPoolExecutor:
        return self._executor

    @property
    def pending_count(self) -> int:
        return self._pending_count

    async def recognize(self, image_data: bytes) -> str:
        """识别验证码图片

        Args:
            image_data: 验证码图片二进制数据（PNG/JPG/GIF）

        Returns:
            识别出的验证码文本

        Raises:
            CaptchaServiceBusyError: 队列已满
            CaptchaServiceUnavailableError: OCR 引擎不可用
            CaptchaError: 其他识别错误
        """
        with self._lock:
            if self._pending_count >= self.queue_limit:
                logger.warning(
                    "验证码队列已满: pending=%d, limit=%d",
                    self._pending_count,
                    self.queue_limit,
                )
                raise CaptchaServiceBusyError(
                    f"队列已满 {self._pending_count}/{self.queue_limit}"
                )
            self._pending_count += 1

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._executor,
                self._recognize_sync,
                image_data,
            )
            return result
        finally:
            with self._lock:
                self._pending_count -= 1

    def _recognize_sync(self, image_data: bytes) -> str:
        """同步识别（在线程池中执行）"""
        ocr = self._get_ocr()
        try:
            result = ocr.classification(image_data)
            if not result:
                raise CaptchaError("OCR 返回空结果")
            # 清理结果：去空格，转大写（验证码通常不区分大小写）
            cleaned = result.strip()
            logger.debug("验证码识别结果: %s", cleaned)
            return cleaned
        except (CaptchaError, CaptchaServiceUnavailableError):
            raise
        except Exception as e:
            logger.error("验证码识别异常: %s", e)
            raise CaptchaError(f"识别失败: {e}")

    # 保留旧接口兼容
    def _call_ocr_service(self, image_data: bytes) -> str:
        """兼容旧接口，内部转调本地识别"""
        return self._recognize_sync(image_data)

    def shutdown(self) -> None:
        """关闭线程池"""
        self._executor.shutdown(wait=False)

"""日志模块：控制台 + 文件(TimedRotatingFileHandler)，文件初始化失败不影响主流程。"""
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from app.config import LOG_DIR, LOG_FILE_NAME, LOG_FORMAT, LOG_DATE_FORMAT, LOG_BACKUP_DAYS


class _SafeRotatingFileHandler(TimedRotatingFileHandler):
    """午夜轮转文件处理器（Windows 加固版）。

    Windows 下若日志文件被其他进程占用（如同机运行了多个 backend 实例），
    轮转时的 rename 会抛 PermissionError。此处捕获该异常并**静默降级**：
    放弃本次轮转、继续写当前文件，且本进程内不再反复重试（避免刷屏），
    符合"文件日志失败不影响主流程"的约定。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rollover_suppressed = False

    def shouldRollover(self, record):
        if self._rollover_suppressed:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError as exc:  # noqa: BLE001 - 文件被占用，降级而非崩溃
            self._rollover_suppressed = True
            sys.stderr.write(
                f"[logger] 日志轮转已跳过（文件被其他进程占用，不影响服务）: {exc}\n"
            )
            # super 在 rename 前已关闭 stream，失败后需重新打开以保证后续日志可写
            if self.stream is None or getattr(self.stream, "closed", True):
                try:
                    self.stream = self._open()
                except OSError:
                    pass


def get_logger(name: str) -> logging.Logger:
    """获取配置好的 logger（控制台 + 文件，文件失败仅告警不阻断）。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # 控制台输出
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件输出（午夜轮转，失败不阻断主流程）
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = _SafeRotatingFileHandler(
            LOG_DIR / LOG_FILE_NAME,
            when="midnight",
            backupCount=LOG_BACKUP_DAYS,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as exc:  # noqa: BLE001 - 文件日志失败不应阻断服务
        logger.warning("文件日志初始化失败，仅使用控制台: %s", exc)
    return logger

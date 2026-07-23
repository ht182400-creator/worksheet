"""日志模块：控制台 + 文件(TimedRotatingFileHandler)，文件初始化失败不影响主流程。"""
import logging
from logging.handlers import TimedRotatingFileHandler

from app.config import LOG_DIR, LOG_FILE_NAME, LOG_FORMAT, LOG_DATE_FORMAT, LOG_BACKUP_DAYS


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
        file_handler = TimedRotatingFileHandler(
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

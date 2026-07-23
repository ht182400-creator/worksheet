"""后端配置常量（禁止硬编码魔法值，统一 UPPER_SNAKE_CASE）。"""
from pathlib import Path

# 服务配置
SERVICE_HOST = "0.0.0.0"
SERVICE_PORT = 8000
API_V1_PREFIX = "/api/v1"

# 日志配置（项目规范：精确到毫秒 + TimedRotatingFileHandler 午夜轮转）
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE_NAME = "work_order_system.log"
LOG_FORMAT = "[%(asctime)s.%(msecs)03d] %(levelname)-5s %(name)s:%(lineno)d  %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_BACKUP_DAYS = 30

# 业务常量（对应 V5.0 §25/§4.9/§26）
DEFAULT_WITHDRAW_WINDOW_MINUTES = 30   # 报工撤回窗口（§4.9.2 / M5-12）
ANOMALY_QTY_DEVIATION_RATIO = 0.30     # 异常数量偏差阈值（§4.9.5.2）
BIGSCREEN_STALE_WARN_SECONDS = 30      # 大屏新鲜度警告阈值（BR-19 / D5）
BIGSCREEN_SNAPSHOT_SECONDS = 300       # 大屏静态快照阈值（5min）
QRCODE_MIN_DPI = 300                   # 二维码打印分辨率下限（BR-15）
QRCODE_MIN_SIZE_MM = 30                # 二维码最小尺寸（BR-15）
QRCODE_BATCH_MAX = 100                 # 批量生成上限（M3-02）
OCR_DOC_CONFIDENCE_THRESHOLD = 0.60    # 整单置信度阈值（BR-20）
OCR_KEY_FIELD_ERROR_LIMIT = 3          # 关键字段错上限（BR-20）
REPORT_OVERFLOW_CODE = "BIZ_REPORT_OVERFLOW"
STATE_ILLEGAL_CODE = "BIZ_STATE_ILLEGAL"
VERSION_CONFLICT_CODE = "BIZ_VERSION_CONFLICT"
WITHDRAW_EXPIRED_CODE = "BIZ_WITHDRAW_EXPIRED"
PERMISSION_DENY_CODE = "BIZ_PERMISSION_DENY"

# 演示/骨架业务常量（生产数据应来自 order_process.required_qty 或配置中心）
DEFAULT_DEMO_REQUIRED_QTY = 100       # 演示工序要求量（超报拦截基线，BR-05）
OCR_SAMPLE_STATUS = "DONE"            # OCR 轮询演示终态（§25.2.1）
OCR_SAMPLE_DOC_CONFIDENCE = 0.95      # OCR 演示整单置信度
BIGSCREEN_PUSH_INTERVAL_SECONDS = 3   # 大屏 SSE 推送间隔（≤5s，§25.2.8 / BR-19）
ORDER_NOT_FOUND_CODE = "BIZ_ORDER_NOT_FOUND"
REPORT_NOT_FOUND_CODE = "BIZ_REPORT_NOT_FOUND"

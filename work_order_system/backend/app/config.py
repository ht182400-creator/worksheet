"""后端配置常量（禁止硬编码魔法值，统一 UPPER_SNAKE_CASE）。"""
import os
from pathlib import Path

# 本地开发从 backend/.env 读取密钥等环境变量（生产改用系统环境变量/密钥管理注入）。
# 缺失 python-dotenv 或 .env 不存在时静默跳过，不影响 CI / 容器环境。
# 注意：load_dotenv 必须在所有 os.getenv(...) 调用之前执行，确保 .env 值生效。
try:
    from dotenv import load_dotenv

    _ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=_ENV_FILE)
except Exception:  # noqa: BLE001 - 依赖/文件缺失不应阻断启动
    pass

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
WORK_ORDER_DUPLICATE_CODE = "BIZ_WORK_ORDER_DUPLICATE"   # 重复工单号拦截（禁止同 display_no 重复入库回填）

# 演示/骨架业务常量（生产数据应来自 order_process.required_qty 或配置中心）
DEFAULT_DEMO_REQUIRED_QTY = 100       # 演示工序要求量（超报拦截基线，BR-05）

# OCR / PDF 解析（对应 M1-01/M1-03/M1-09/M1-11 / BR-17/BR-20）
OCR_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"  # 上传文件落盘
OCR_MAX_PAGES = 50                    # 单文件最大解析页数（防超长 PDF 卡死）
OCR_AUTO_PASS_CONFIDENCE = 0.95       # 整单置信度 ≥ 此值自动通过（M1-11）
OCR_MANUAL_REVIEW_CONFIDENCE = 0.70   # 整单置信度 < 此值强制人工重录（M1-11）
OCR_FIELD_FOUND_CONFIDENCE = 0.90     # 字段命中标签+值的基础置信度
OCR_FIELD_EMPTY_CONFIDENCE = 0.40     # 命中标签但值缺失的置信度
OCR_FIELD_NUMERIC_MISMATCH = 0.60     # 期望数值但值非数值的降级置信度
OCR_FIELD_VALUE_PATTERN_CONFIDENCE = 0.50  # 标签未识别但值模式命中时的置信度（显式低于标签命中，提示为推断值）

# 后端原生 Tesseract OCR（方案 A：识别率优于浏览器 tesseract.js）
# 核心提升点：服务器原生引擎（无 WASM 性能限制）+ 图像预处理（放大/灰度/二值化）
# + 显式 PSM + 最新语言包。需系统安装原生 Tesseract-OCR 并配置中文包。
OCR_LANG = "chi_sim+eng"               # 中文 + 英文/数字语言包
OCR_PSM = 6                            # PSM=6：假设为统一文本块（工单截图/扫描件多为单栏）
OCR_DPI = 300                         # PDF 逐页渲染为图片的 DPI（越高越清晰但越慢）
OCR_UPSCALE = 3.0                     # 放大倍数（工单流程卡标签字号小，2x 易被误识为近形错字）
OCR_UPSCALE_MIN_WIDTH = 1600         # 原图宽度低于此值时至少放大到该宽度（954px 图将被放大到 ~1600+）
OCR_BINARIZE_METHOD = "otsu"         # 二值化方法：otsu(自动阈值，适配不同光照) / fixed(固定阈值)
OCR_SHARPEN = True                   # 二值化前轻度锐化，增强细小笔画（提升小字号中文召回）                     # 小图/截图放大倍数，提升输入像素密度
OCR_BINARIZE_THRESHOLD = 140          # 灰度二值化阈值（<阈值判为背景，>=为文字）
OCR_PSM_CANDIDATES = (3, 6, 4, 11)   # 自适应候选 PSM：按质量分择优（M1-01 表单 OCR）
OCR_REMOVE_GRID_LINES = True         # 是否去除工单表格网格线（提升表单识别率，M1-01）
OCR_GRID_LINE_RATIO = 0.6            # 判定为网格线的长度占比（≥此比例视为线而非文字）
OCR_DESPECKLE_AREA = 30              # 去除小于该像素面积的噪点连通域（防误删细笔画）
OCR_ENGINE_SERVER = "server-tesseract"    # 后端原生识别（方案 A）
OCR_ENGINE_PDF_LAYER = "pdf-text-layer"   # PDF 文本层直抽（非 OCR）

# OCR 任务分阶段进度（异步后台解析 + 真实进度条，避免前端长阻塞且不可见）
OCR_STAGE_QUEUED = "QUEUED"              # 已入队，等待解析线程启动
OCR_STAGE_TEXT_LAYER = "TEXT_LAYER"      # 正在提取 PDF 文本层（含文本层时此步即出结果）
OCR_STAGE_RENDER_OCR = "RENDER_OCR"      # 渲染页面 + 逐页 OCR 识别（纯扫描件/图片）
OCR_STAGE_PARSE_FIELDS = "PARSE_FIELDS"  # 规则化解析工单字段
OCR_STAGE_DONE = "DONE"                  # 解析完成
OCR_STAGE_FAILED = "FAILED"              # 解析失败（无文本层/空结果/异常）
OCR_PCT_TEXT_LAYER = 10                  # 文本层提取阶段进度基线（0-100）
OCR_PCT_RENDER_OCR_MIN = 30              # 渲染+OCR 阶段起始进度（逐页递增至此区间）
OCR_PCT_RENDER_OCR_MAX = 85              # 渲染+OCR 阶段结束进度（交接给字段解析）
OCR_PCT_PARSE_FIELDS = 90                # 字段解析阶段进度
OCR_PCT_DONE = 100                       # 完成

# 原生 Tesseract 二进制候选路径（不在 PATH 时按顺序探测，首个存在者即用）。
# 本机实测装在 D:\Program Files (x86)\Tesseract-OCR；其余为常规默认位置。
# 也可通过环境变量 TESSERACT_CMD 显式指定，优先级最高。
TESSERACT_CMD_CANDIDATES = (
    r"D:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"D:\Program Files\Tesseract-OCR\tesseract.exe",
)

BIGSCREEN_PUSH_INTERVAL_SECONDS = 3   # 大屏 SSE 推送间隔（≤5s，§25.2.8 / BR-19）
ORDER_NOT_FOUND_CODE = "BIZ_ORDER_NOT_FOUND"
REPORT_NOT_FOUND_CODE = "BIZ_REPORT_NOT_FOUND"

# 微信小程序订阅消息推送（工单→工人，§新增）。WX_PUSH_ENABLED=False 时全链路 no-op。
# 密钥经环境变量注入，禁止硬编码；配置 appid/secret 并确认模板后改 True。
WX_PUSH_ENABLED = False                # 总开关：配置 appid/secret 且确认模板后改 True
WX_APPID = os.getenv("WX_APPID", "")               # 小程序 appid（环境变量注入，避免硬编码密钥）
WX_APPSECRET = os.getenv("WX_APPSECRET", "")       # 小程序 secret（环境变量注入）
WX_SUBSCRIBE_TEMPLATE_ID = os.getenv("WX_SUBSCRIBE_TEMPLATE_ID", "")  # 订阅消息模板 ID
WX_ACCESS_TOKEN_TTL_SECONDS = 7200     # 微信 access_token 官方有效期（2h）
WX_TOKEN_REFRESH_THRESHOLD = 300       # 距过期 <300s 即刷新（提前量，防临界失效）
WX_HTTP_TIMEOUT = 5                    # 微信接口 HTTP 超时（秒）
WX_SUBSCRIBE_PAGE = "pages/todo/todo"  # 点击订阅消息跳转的小程序页面（按实际页面路径调整）
# 订阅消息模板字段映射（keyword→格式化串）：需与小程序后台所选模板的关键词顺序/类型一致。
# 微信订阅消息单字段值上限 20 字；未匹配到的占位符置空。
WX_TEMPLATE_FIELDS = {
    "thing1": "{display_no}",
    "thing2": "{product}",
    "number1": "{plan_qty}",
    "thing3": "{event_hint}",
}

# 工单状态常量（状态机权威源见 state_machine.py / §03）
WORK_ORDER_STATE_DISPATCHED = 3        # 已分发（GEN_QRCODE）：触发"派活"推送（两处都推之一）

// 小程序端配置（部署前必须替换以下占位符；不要将真实 secret 写进前端）
//
// 占位符清单、获取方式与填后验证见 docs/07_小程序占位符与配置处理手册.md。
//
// BASE_URL: 后端服务地址。把 192.168.1.100 换成运行后端的电脑「局域网 IP」，
//           端口与后端 SERVICE_PORT(8000) + API_V1_PREFIX(/api/v1) 对应。
//           微信开发者工具需勾选「详情 → 本地设置 → 不校验合法域名」才能访问 http 地址。
// TEMPLATE_ID: 订阅消息模板 ID（非敏感），须与后端 WX_SUBSCRIBE_TEMPLATE_ID 完全一致。
//           小程序后台「订阅消息 → 我的模板」获取；并据模板关键词对齐后端 WX_TEMPLATE_FIELDS。
// TENANT_ID: 演示用固定值 t1；生产应由登录态/企业映射决定。
const BASE_URL = 'http://172.28.144.1:8000/api/v1';
const TEMPLATE_ID = 'TEMPLATE_ID_HERE';
const TENANT_ID = 't1';
const QRCODE_DEEPLINK_SCHEME = 'wo://report'; // 扫码报工深链 scheme（须与后端 config.QRCODE_DEEPLINK_SCHEME 保持一致）

// 占位符自检：检测到未替换的占位符时在控制台告警，避免漏填（详见 docs/07）。
// 真机/真值就位后，将 BASE_URL 改成真实局域网 IP、TEMPLATE_ID 改成真实模板 ID 即可消除告警。
if (TEMPLATE_ID === 'TEMPLATE_ID_HERE' || BASE_URL.indexOf('192.168.1.100') !== -1) {
  // eslint-disable-next-line no-console
  console.warn('[config] 检测到占位符未替换：请修改 utils/config.js 的 BASE_URL（局域网 IP）/ TEMPLATE_ID 为真实值');
}

module.exports = { BASE_URL, TEMPLATE_ID, TENANT_ID };

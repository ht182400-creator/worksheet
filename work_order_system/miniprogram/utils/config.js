// 小程序端配置（按需修改；不要将真实 secret 写进前端）
//
// BASE_URL: 后端服务地址。在本机跑后端后，把 192.168.1.100 换成电脑的局域网 IP，
//           端口与后端 SERVICE_PORT(8000) + API_V1_PREFIX(/api/v1) 对应。
//           微信开发者工具需勾选「详情 → 本地设置 → 不校验合法域名」才能访问 http 地址。
const BASE_URL = 'http://192.168.1.100:8000/api/v1';

// 订阅消息模板 ID：必须与后端 config.py 的 WX_SUBSCRIBE_TEMPLATE_ID 完全一致。
// 在小程序后台「订阅消息 → 我的模板」获取，并把 WX_TEMPLATE_FIELDS 对齐该模板的关键词。
const TEMPLATE_ID = 'TEMPLATE_ID_HERE';

// 租户 ID：演示用固定值 t1；生产应由登录态/企业映射决定。
const TENANT_ID = 't1';

module.exports = { BASE_URL, TEMPLATE_ID, TENANT_ID };

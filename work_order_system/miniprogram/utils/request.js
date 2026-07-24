// 后端请求封装（Promise 化 wx.request），统一解析后端 {code,data,message,traceId} 响应体。
const { BASE_URL } = require('./config.js');

/**
 * 发起后端请求。
 * @param {string} method GET/POST
 * @param {string} path 相对路径（不含 BASE_URL）
 * @param {object} [data] 请求体
 * @returns {Promise<any>} 成功时 resolve 响应体中的 data 字段
 */
function request(method, path, data) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + path,
      method: method,
      data: data,
      header: { 'Content-Type': 'application/json' },
      success: (res) => {
        const body = res.data || {};
        // 后端统一成功约定：HTTP 2xx 且 code === '0'
        if (res.statusCode >= 200 && res.statusCode < 300 && body.code === '0') {
          resolve(body.data);
        } else {
          reject(new Error(body.message || ('HTTP ' + res.statusCode)));
        }
      },
      fail: (err) => reject(new Error((err && err.errMsg) || 'network error')),
    });
  });
}

module.exports = {
  get: (path) => request('GET', path),
  post: (path, data) => request('POST', path, data),
};

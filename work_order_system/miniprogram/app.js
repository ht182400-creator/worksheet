// 小程序全局逻辑：启动时登录换取 openid、缓存订阅消息配置。
const { BASE_URL } = require('./utils/config.js');
const request = require('./utils/request.js');

App({
  globalData: {
    openid: '',
    baseUrl: BASE_URL,
    subscribeConfig: null, // {enabled, template_id, page}
  },

  onLaunch() {
    // 已有 openid 则复用；否则走登录换 openid
    const cached = wx.getStorageSync('openid');
    if (cached) {
      this.globalData.openid = cached;
    } else {
      this.login();
    }
    // 拉取订阅消息配置（模板 id 等），缓存到 globalData 供页面使用
    request.get('/wechat/subscribe-config')
      .then((cfg) => { this.globalData.subscribeConfig = cfg; })
      .catch(() => { /* 配置不可用不影响登录，推送环节再降级 */ });
  },

  /**
   * wx.login -> code -> 后端 /wechat/code2session -> openid。
   * 小程序无法直连微信拿 openid，必须经后端用 code 换取。
   * @returns {Promise<string>} openid
   */
  login() {
    return new Promise((resolve, reject) => {
      wx.login({
        success: (res) => {
          if (!res.code) {
            reject(new Error('wx.login 未返回 code'));
            return;
          }
          request.post('/wechat/code2session', { code: res.code })
            .then((data) => {
              this.globalData.openid = data.openid;
              wx.setStorageSync('openid', data.openid);
              resolve(data.openid);
            })
            .catch((e) => reject(e));
        },
        fail: (e) => reject(new Error((e && e.errMsg) || 'wx.login 失败')),
      });
    });
  },
});

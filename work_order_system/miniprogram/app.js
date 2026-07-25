// 小程序全局逻辑：启动时登录换取 openid、缓存订阅消息配置。
const { BASE_URL, TENANT_ID } = require('./utils/config.js');
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
              const openid = data.openid;
              this.globalData.openid = openid;
              wx.setStorageSync('openid', openid);
              // 登录后确保工人已在后端注册（仅当不存在时落地，不覆盖已有订阅余量）。
              // 注册失败不阻塞登录（降级：待办页仍可轮询，订阅授权后续可补）。
              this.ensureRegistered(openid)
                .then(() => resolve(openid))
                .catch(() => resolve(openid));
            })
            .catch((e) => reject(e));
        },
        fail: (e) => reject(new Error((e && e.errMsg) || 'wx.login 失败')),
      });
    });
  },

  /**
   * 确保工人已在后端注册：先查 by-openid，不存在(404)才 POST /workers 落地(quota=0)，
   * 已存在则跳过以保留其订阅余量（避免重复注册覆盖已授权的 quota）。
   * @param {string} openid 微信 openid
   * @returns {Promise<void>}
   */
  ensureRegistered(openid) {
    return request.get('/workers/by-openid/' + openid)
      .then(() => { /* 已注册：保留现有余量，无需动作 */ })
      .catch(() => request.post('/workers', { openid: openid, name: '', tenant_id: TENANT_ID }));
  },

  /**
   * 退出登录：清除本地 openid 缓存与全局态（§新增首页退出按钮）。
   * 注意：同一微信账号 openid 由微信决定、不会变，重新登录会复用同一后端工人记录
   * （设计预期，保留其订阅余量），故「退出」等价于清本地登录态而非切换微信用户。
   */
  logout() {
    this.globalData.openid = '';
    wx.removeStorageSync('openid');
  },
});

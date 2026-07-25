// 首页：确认登录态 + 授权订阅并注册 + 进入待办。
const app = getApp();
const request = require('../../utils/request.js');
const { TEMPLATE_ID, TENANT_ID } = require('../../utils/config.js');

Page({
  data: {
    openid: '',
    name: '',
    workerNames: [],
    registered: false,
    quota: 0,
    statusText: '未登录',
  },

  onShow() {
    this.refreshStatus();
    this.loadWorkers();
  },

  // 刷新登录态与订阅余量展示
  refreshStatus() {
    const openid = app.globalData.openid || wx.getStorageSync('openid');
    this.setData({ openid: openid || '', statusText: openid ? '已登录' : '未登录' });
    if (openid) {
      request.get('/workers/by-openid/' + openid)
        .then((d) => this.setData({ registered: true, quota: d.subscribe_quota }))
        .catch(() => this.setData({ registered: false, quota: 0 }));
    }
  },

  onNameInput(e) {
    this.setData({ name: e.detail.value });
  },

  // 拉取已注册工人姓名列表，供姓名输入框「下拉选择」使用（§新增：输入+下拉二合一）
  loadWorkers() {
    request.get('/workers')
      .then((d) => {
        // name 为空时回退为「微信用户(后6位)」而非千篇一律「未命名」，避免多人下拉无法区分
        const list = (d || []).map((w) => w.name || ('微信用户(' + (w.openid || '').slice(-6) + ')'));
        this.setData({ workerNames: list });
      })
      .catch(() => { /* 列表拉取失败不影响手输模式 */ });
  },

  // 下拉选择姓名回填到输入框（选中后仍可继续手改，两种模式并存）
  onPickWorker(e) {
    const list = this.data.workerNames;
    const idx = e.detail.value;
    if (idx >= 0 && idx < list.length) {
      const picked = list[idx];
      // 选中项是「未登记姓名」占位时不回填（避免覆盖用户输入），提示用户手输
      if (picked && picked.indexOf('微信用户(') === 0) {
        wx.showToast({ title: '该工人未登记姓名，请手输', icon: 'none' });
        return;
      }
      this.setData({ name: picked });
    }
  },

  // 在交互点引导用户授权订阅消息（一次性订阅，每次授权=1 条），授权后上报后端注册
  onSubscribeAndRegister() {
    const tmplId = (app.globalData.subscribeConfig && app.globalData.subscribeConfig.template_id) || TEMPLATE_ID;
    if (!tmplId || tmplId === 'TEMPLATE_ID_HERE') {
      wx.showToast({ title: '请先配置模板ID', icon: 'none' });
      return;
    }
    const openid = app.globalData.openid || wx.getStorageSync('openid');
    if (!openid) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => {
        // res[tmplId] === 'accept' | 'reject' | 'ban'
        const accepted = res[tmplId] === 'accept' ? 1 : 0;
        this.register(openid, accepted);
      },
      fail: (err) => {
        // 用户拒绝/系统限制：仍注册（quota=0），推送环节降级，待办页轮询兜底
        wx.showToast({ title: '订阅授权失败，已降级为轮询', icon: 'none' });
        this.register(openid, 0);
      },
    });
  },

  // 注册/更新工人，上报授权余量
  register(openid, granted) {
    request.post('/workers', {
      openid: openid,
      name: this.data.name || '',
      tenant_id: TENANT_ID,
      subscribe_quota: granted,
    }).then((d) => {
      this.setData({ registered: true, quota: d.subscribe_quota });
      wx.showToast({ title: granted ? '已授权并注册' : '已注册(未授权)', icon: 'success' });
    }).catch((e) => {
      wx.showToast({ title: '注册失败: ' + e.message, icon: 'none' });
    });
  },

  // 退出登录：清本地 openid 缓存 + 重置首页态（§新增）
  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '将清除本地登录态，确定吗？（同一微信账号 openid 不变，重新登录复用同一工人记录）',
      success: (res) => {
        if (res.confirm) {
          app.logout();
          this.setData({
            openid: '', name: '', workerNames: [],
            registered: false, quota: 0, statusText: '未登录',
          });
          wx.showToast({ title: '已退出', icon: 'success' });
        }
      },
    });
  },

  // 登录（退出后重新登录 / 首次未登录触发，§新增）
  onLogin() {
    this.setData({ statusText: '登录中...' });
    app.login()
      .then(() => { this.refreshStatus(); this.loadWorkers(); })
      .catch(() => this.setData({ statusText: '登录失败', openid: '' }));
  },

  // 授权手机号并绑定（§新增 getPhoneNumber）：工人点按钮授权 → 后端解密真实手机号并与 openid 关联
  onGetPhone(e) {
    const openid = app.globalData.openid || wx.getStorageSync('openid');
    if (!openid) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    // 微信返回 e.detail.code（成功）或 errMsg 含 deny（用户拒绝）
    if (!e.detail || !e.detail.code) {
      wx.showToast({ title: '未授权手机号', icon: 'none' });
      return;
    }
    request.post('/workers', {
      openid: openid,
      name: this.data.name || '',
      tenant_id: TENANT_ID,
      phone_code: e.detail.code,
      // 携带当前订阅余量，避免「仅绑定手机号」的二次注册把已有授权清零（§修复同款坑）
      subscribe_quota: this.data.quota || 0,
    }).then((d) => {
      this.setData({ registered: true, quota: d.subscribe_quota });
      wx.showToast({ title: '手机号已绑定', icon: 'success' });
    }).catch((err) => {
      wx.showToast({ title: '绑定失败: ' + err.message, icon: 'none' });
    });
  },

  goTodo() {
    wx.navigateTo({ url: '/pages/todo/todo' });
  },
});

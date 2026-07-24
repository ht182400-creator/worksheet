// 首页：确认登录态 + 授权订阅并注册 + 进入待办。
const app = getApp();
const request = require('../../utils/request.js');
const { TEMPLATE_ID, TENANT_ID } = require('../../utils/config.js');

Page({
  data: {
    openid: '',
    name: '',
    registered: false,
    quota: 0,
    statusText: '未登录',
  },

  onShow() {
    this.refreshStatus();
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

  goTodo() {
    wx.navigateTo({ url: '/pages/todo/todo' });
  },
});

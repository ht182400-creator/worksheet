// 待办页：按 openid 拉取"我的待办" + 定时轮询兜底（订阅消息未授权时的补偿通道）。
const app = getApp();
const request = require('../../utils/request.js');
const { TEMPLATE_ID, TENANT_ID } = require('../../utils/config.js');

const POLL_INTERVAL_MS = 15000; // 15s 轮询兜底

Page({
  data: {
    tasks: [],
    loading: false,
    lastUpdate: '',
  },

  onShow() {
    this.loadTasks();
    this.timer = setInterval(() => this.loadTasks(), POLL_INTERVAL_MS);
  },

  onHide() {
    this.clearTimer();
  },

  onUnload() {
    this.clearTimer();
  },

  clearTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  },

  // 拉取"我的待办"：按 openid 过滤工单（后端 pending_tasks?assignee_openid=）
  loadTasks() {
    const openid = app.globalData.openid || wx.getStorageSync('openid');
    if (!openid) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    this.setData({ loading: true });
    request.get('/pending-tasks?assignee_openid=' + encodeURIComponent(openid))
      .then((tasks) => {
        this.setData({ tasks: tasks, loading: false, lastUpdate: this.now() });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: '加载失败: ' + e.message, icon: 'none' });
      });
  },

  now() {
    const d = new Date();
    const p = (n) => (n < 10 ? '0' + n : '' + n);
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  },

  // 在页面交互点引导再次订阅（补充授权余量）
  onSubscribe() {
    const tmplId = (app.globalData.subscribeConfig && app.globalData.subscribeConfig.template_id) || TEMPLATE_ID;
    if (!tmplId || tmplId === 'TEMPLATE_ID_HERE') {
      wx.showToast({ title: '请先配置模板ID', icon: 'none' });
      return;
    }
    const openid = app.globalData.openid || wx.getStorageSync('openid');
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => {
        if (res[tmplId] !== 'accept') {
          wx.showToast({ title: '未授权', icon: 'none' });
          return;
        }
        // 在现有余量基础上 +1 后上报（避免覆盖已有授权）
        request.get('/workers/by-openid/' + openid)
          .then((d) => {
            const newQuota = (d.subscribe_quota || 0) + 1;
            return request.post('/workers', { openid: openid, tenant_id: TENANT_ID, subscribe_quota: newQuota });
          })
          .then(() => wx.showToast({ title: '授权+1', icon: 'success' }))
          .catch((e) => wx.showToast({ title: '更新失败: ' + e.message, icon: 'none' }));
      },
    });
  },

  onPullDownRefresh() {
    this.loadTasks();
    wx.stopPullDownRefresh();
  },
});

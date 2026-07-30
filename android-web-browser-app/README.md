# Nova Android Browser

这是一个独立的新项目，不会修改上级目录里的地面站 UI 文件。

## 功能

- 安卓手机优先的浏览器界面
- 地址栏搜索和网址访问
- 多标签页管理
- 首页快捷入口
- 历史记录和收藏夹
- PWA manifest 与 service worker，可添加到安卓主屏幕
- 内嵌网页预览，以及受限网站的外部打开按钮

## 运行

直接打开 `index.html` 可以查看界面。若要测试 PWA 安装和 service worker，请在本目录启动自带静态服务器：

```powershell
node server.js
```

然后在安卓设备或桌面浏览器访问：

```text
http://127.0.0.1:8098
```

## 说明

网页版应用无法像原生 Android WebView 那样绕过网站安全策略。很多网站会通过 `X-Frame-Options` 或 `Content-Security-Policy` 禁止被 iframe 嵌入，所以应用提供了“打开”按钮，用系统浏览器新窗口访问真实网页。

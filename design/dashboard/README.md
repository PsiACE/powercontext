# Dashboard 最小表达与实现基线

只实现三种页面：**首页、经验与依据、用量**。用户提供的三张图约束视觉，当前代码与 OpenAPI 约束表达，Tabler 约束组件。验收重点是用户能否理解产品，以及这些页面能否成为后续实现的基础。

## 打开

```bash
uv run --script design/dashboard/app.py
```

[首页](http://127.0.0.1:8765/home) · [经验与依据](http://127.0.0.1:8765/experience) · [用量](http://127.0.0.1:8765/usage)

首次启动由 uv 安装脚本内的依赖；静态资源全部保存在本地。原型使用固定示例和 2026-09-07 UTC 观察日，不连接真实服务，不需要配置模型或组织。没有写入路由、会话状态和业务 API 变更。

## 最小阅读链路

- 首页先展示交接中记录的情况与下一步，再展示记事与方法。侧栏的交接、记事、方法定位到这些内容区。
- 从经验进入正文，对照两份原始材料，可以切换材料、打开完整记录、关闭侧栏和返回。技能只提供首页中的折叠阅读示例。
- 用量回答准备了多少内容、哪些记录可比较、模型报告了多少用量。周期切换与每日表格用于核对统计。
- 切换为“客户访谈”，检查同一组产品词是否依然成立。

保存、修订、审核、组织管理、技能分发和全面 API 接入均不属于本次实现范围；界面不放这些动作的占位按钮。

## 基础结构

| 文件 | 职责 |
| --- | --- |
| `app.py` | 只读 HTML 路由、完整页/片段响应、模板上下文 |
| `fixtures.py`、`content.json` | 两组示例和统一统计数据；与页面路由分离 |
| `templates/base.html`、`workspace.html` | 资源装载、导航、页面容器、反馈与依据抽屉 |
| `templates/home.html`、`experience.html`、`usage.html` | 三种页面的内容组合 |
| `templates/macros.html` | 共用的 Tabler 组件标记、经验卡片和图表容器 |
| `static/layout.css` | 复现图例的主题变量、尺寸、间距和并排阅读版式 |
| `static/interactions.js` | HTMX 衔接、Tabler 抽屉和 ApexCharts 实例生命周期 |

**Tabler 有的组件就使用 Tabler。** 导航、面包屑、卡片、列表、表单、按钮、Accordion、Offcanvas、Alert、Spinner、Progress、Table 均直接复用。图表采用 Tabler 的 ApexCharts 集成，不绘制自定义柱状图或提示框。

固定资源版本为 Tabler Core 1.4.0、Tabler Icons 3.31.0、HTMX 2.0.4，以及 Tabler 1.4.0 包配置指定的 ApexCharts 3.54.1。资源和许可证在 `static/vendor/`。

## 评估与验收

[产品表达与范围](AUDIT.md) · [实现质量基线](QUALITY.md)

图例对应截图：[首页](previews/home.png) · [经验与依据](previews/experience.png) · [用量](previews/usage.png) · [小屏](previews/mobile.png)

异常数据通过 URL 参数供开发检查，不占用产品界面：

- `?state=empty`：没有内容。
- 经验页 `?state=missing`：依据读取失败。
- 用量页 `?state=unknown`、`uncomparable`、`negative`、`unavailable`：未知值、无可比较记录、负差值、读取失败。
- `?ablation=evidence`、`chart`、`navigation`：分别移除依据入口、图表或图标，检验其贡献。

这些是有限的质量样例，不代表需要为所有业务条件建立独立功能。

```bash
uv run --script design/dashboard/checks/contract.py
npm install --prefix /tmp/pc-design-browser playwright@1.61.1
NODE_PATH=/tmp/pc-design-browser/node_modules node design/dashboard/checks/browser.cjs
```

浏览器检查需要 Chromium；新环境可以运行 `/tmp/pc-design-browser/node_modules/.bin/playwright install chromium`。检查地址可用 `PC_DESIGN_URL` 覆盖。

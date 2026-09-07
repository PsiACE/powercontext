# Dashboard 实现质量基线

## 组件与样式

1. **Tabler 已有的组件必须复用，不能自行实现替代品。** 先查已选版本的组件和插件，再组合页面。不得另写导航、折叠、抽屉、提示框、图表、表格或加载状态。
2. Jinja2 macro 只复用组件标记与内容组合，不创建平行的组件框架。自定义 JS 仅做数据映射、初始化、销毁与 HTMX 衔接。
3. 用户提供的三张图是视觉基准。CSS 只修改主题变量、字体大小、宽度、间距和阅读布局，不另设视觉主题。不能用大范围重写组件内部样式换取截图相似度。
4. 第三方资源固定版本并保留许可证，启动后不依赖 CDN。

| 内容 | 直接复用 |
| --- | --- |
| 页面导航和当前位置 | Tabler Navbar、Nav、Breadcrumb |
| 范围选择 | Tabler Form Select、Input Group、Button |
| 内容概览 | Tabler Card、List Group、响应式 Grid |
| 交接边界、技能、计算方式、每日汇总 | Tabler Accordion |
| 原始依据 | Tabler Offcanvas、Nav Tabs |
| 空态、读取错误、加载中 | Tabler Empty、Alert、Spinner |
| 日序列图与 tooltip | Tabler ApexCharts 集成 |
| 范围差值与数值核对 | Tabler Progress、Table |

Tabler 的图表文档明确由 ApexCharts 绘图，调用方负责初始化。所选 Tabler Core 1.4.0 的 package.json 固定 ApexCharts 3.54.1，libs.json 也列出该插件；本原型沿用这组版本。

参考：[图表](https://docs.tabler.io/ui/plugins/chart)、[Accordion](https://docs.tabler.io/ui/components/accordion)、[Offcanvas](https://docs.tabler.io/ui/components/offcanvas)。具体可用结构以本地固定版本为准。

## 实现边界

- 三个页面共享一份 shell；每个页面有独立模板，禁止不断增长的页面条件分支。
- 示例数据与 HTML 路由分离。生产接入时替换数据来源，保留视图边界；当前不创建通用 adapter、权限框架或业务状态管理器。
- 首页与用量页共用统计数据和图表 macro。汇总从同一批 daily 值得出，不分别写死展示数字。
- 图表挂载与卸载跟随 HTMX 片段生命周期，避免重复实例与残留的窗口监听。
- 模板默认转义，缺失字段直接暴露为开发错误。正文不作为 HTML 或脚本执行。
- 未知页面返回 404；整页与 HTMX 片段都使用 no-store；history restore 返回完整文档。

## 可观察的验收条件

| 维度 | 必须成立 |
| --- | --- |
| 内容心智 | 首页可以指出当时的情况、下一步、记事和方法；经验能返回原始依据 |
| 数据一致 | 日表能汇总到总量，周期切换保留当前范围，未知值、零分母和负差值保持原义 |
| 交互 | 链接可以直达、刷新、前进和后退；HTML 阅读链路不依赖 JS 才能成立 |
| 键盘 | 依据入口可用 Enter 打开，抽屉可用 Escape 关闭，焦点返回触发入口 |
| 错误 | 依据失败在原位置说明；网络失败保留已读页面并显示重试入口，不清空内容 |
| 响应式 | 1536px 桌面遵循图例，390px 小屏不横向溢出，原始依据仍可打开和关闭 |
| 降级 | 图表不可用时仍有数值和每日表格；JS 不可用时，普通链接仍可打开经验与原始记录 |
| 范围控制 | 所有交互都是读取；没有为了展示框架而新增写入、认证、审核和组织管理 |

## 检查入口

`checks/contract.py` 校验最小只读请求与示例内容投影能否匹配当前 OpenAPI。它不是实时服务集成测试，也不宣称覆盖全部接口。

`checks/browser.cjs` 检查阅读链路、范围保持、历史导航、键盘、完整 HTML 降级、网络/依据失败、统计边界及小屏。测试保护用户可观察行为，不对 Tabler 的私有方法或实例调用次数作断言。

截图保存在 `previews/`。视觉检查与上述交互检查共同构成这三种页面的基线，不以支持多少 API 作为完成指标。

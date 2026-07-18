# 供应链驾驶舱/控制塔产品首屏形态调研（V9 二补处置① 的证据底座）

> 2026-07-14。调研方式：WebSearch + WebFetch（官方页面文本抓取）+ 直接 `curl` 抓取官方/第三方页面 HTML、
> 提取真实产品截图的图片直链、用 Read 工具肉眼查看截图内容（而非只读营销文案）——这是本次调研区别于纯文本检索的
> 关键方法：多数厂商"落地页文案"只讲价值主张不讲界面细节，必须找到**真实截图**（官方博客配图 / 咨询伙伴博客里的
> 实操截图 / 官方 YouTube 视频封面）才能回答"中央到底是什么"这类具体问题。
> 标注体例：【官方】厂商自有网站/博客/新闻稿的文字或**真实截图**；【官方-插画】厂商自有素材但确认是风格化营销插画
> /视频缩略图非产品截图（仍能反映视觉语言，但不代表真实像素级布局）；【第三方】非原厂分析/评测/咨询伙伴博客
> （若第三方博客里嵌入的是真实产品截图会标注"第三方-真实截图"）；【推断】综合多条证据的推断，无直接证据。
> 触发背景：驾驶舱 B3 视觉升级把"本体对象等距分层图"（客户/订单/在途/供应商/仓库五层实体聚合，属于系统结构
> 透视镜的语言）用成了驾驶舱中央主视图，Daniel 批评"可读性极差、跟透视镜（builder-console 的 IsometricScene）
> 搞混了"。本调研**只调研业界真实供应链驾驶舱长什么样，不做设计**，作为后续三稿方案的证据底座。
> 本项目现状备查：驾驶舱体征带七区口径见 `apps/api/cockpit.py`（钱/履约/客户/供应商/库存/AI/待拍板），
> 当前中央视图实现见 `apps/cockpit/src/views/Panorama.tsx`（等距 2.5D 分层浮板 + 组块聚合）。

---

## 白话总结（建议先读这节）

这次查了 project44、FourKites、Flexport、Shippeo、Infor Nexus、E2open、Blue Yonder、SAP、Altana 九个
"实时可视性/控制塔"类产品，加 Kinaxis、o9、Anaplan 三个"计划指挥类"产品做对照组，外加 Maersk、Amazon(AWS)
两个"自建控制塔"的公开资料做补充。核心方法是想办法找到**真实产品截图**（不是宣传插画），因为几乎所有厂商的
官网文案都只讲"我们能预测中断、能自动化决策"这种价值主张，几乎不讲"屏幕上到底摆了什么"。

先解释几个后面反复出现的术语（怕你不熟悉，宁可啰嗦也不跳过）：

- **地理地图（geographic map）**：真实世界地图，有经纬度、港口、城市、航线曲线。像手机地图 App 那种。
  好处是直觉——"货在哪儿"一眼看懂；坏处是**信息密度低**（一张世界地图能放的数字很有限）、且"钱""客户满意度"
  这类没有地理坐标的指标没法往上摆。
- **网络拓扑图/知识图谱（network graph）**：不是画真实地理位置，而是画"谁连着谁"——比如"供应商 A 供货给
  工厂 B，工厂 B 发货给客户 C"，画成节点（圆点/方块）和连线，位置是布局算法摆的，不代表真实地理坐标。
  本项目当前 Panorama.tsx 的等距分层图，本质上更接近这一类（但把"对象类型分层"当成了"连线拓扑"，形态上
  两头不靠）。
- **KPI 墙（KPI wall）**：一排数字卡片，比如"准交率 92%""待处理异常 14 条"，没有地图也没有连线，纯数字+
  趋势箭头+颜色。好处是**信息密度最高、什么指标都能塞**；坏处是不直观，看久了会成"一面数字墙"（后面会引一句
  业内人原话："控制塔屏幕不该是一面数字墙"）。
- **异常/工作队列（exception queue）**：一张可排序的列表/表格，每行一个"需要处理的问题"（迟到的货、超支的
  发票……），点一行进详情。这是运营人员每天真正在用的东西，跟"好看的可视化"关系不大，跟"今天要做什么"关系很大。
- **下钻（drill-down）**：点一个东西（地图上的点/列表里的一行/KPI 卡片）之后发生的事——是弹出一个侧边栏
  （不离开当前页）、跳到一个新页面、还是弹一个浮层弹窗。这个选择直接影响"看完还能不能找到自己刚才在哪"。
- **高管视角 vs 运营视角（executive vs operational view）**：同一个产品，给 CEO/CSCO 看的和给一线调度员看的
  是不是同一张屏幕。业界的共识非常一致：**不是同一张**，下面细讲。

**三条最重要的发现**（细节在下面五节，这里先剧透结论）：

1. **"控制塔"≠"地图"。** 只有把"实时物流可视性"当主业的几家（project44、Shippeo、SAP IBP 的一个模块）
   把地理地图放在很显眼的位置；就算是这几家，地图也**从来不是唯一的东西**——地图上永远浮着/贴着 KPI 数字卡片
   和"待处理事项"卡片，地图只是"骨架"不是"全部"。而"计划指挥类"产品（Kinaxis、Anaplan）**明确不用地图**，
   用的是接近 Excel 表格的"工作表"界面——因为它们的工作不是"货在哪"，是"怎么调整计划、比较几个方案"。
2. **没有一家把"全部信息硬塞进一张图"。** 无论选地图还是选 KPI 墙做骨架，KPI 摘要、异常清单、AI 建议这些
   都是**独立的卡片/面板**，浮在骨架上或者贴在骨架边上，从来不会跟骨架画在同一个图层里搅在一起
   （这点对我们现在"等距分层图"的问题诊断特别关键——现在的做法恰恰是想把"公司体征""异常""对象"全画进
   同一张图里，这正是业界不这么干的地方）。
3. **高管看的和运营看的必须是两张不同的屏幕，这是业界共识，没有分歧。** 高管要的是"哪里出问题了、要不要我
   管"，30 秒看完；运营要的是"具体哪一单、下一步按哪个按钮"，需要深度和下钻路径。把这两种需求塞进同一张屏幕，
   业内一篇文章原话是"很多项目就是栽在把这两层当成一张屏幕"。

带着这三条发现，报告最后一节给了三个"七区信息可以怎么摆"的候选方案（地图为中心 / KPI-异常墙为中心 / 两者
可切换的混合方案），每个都附证据和 trade-off，**不替你们做最终选择**——留给主会话综合裁决。

---

## 一、首屏中央主视图的形态谱系（逐产品）

### 1. project44（Movement 平台）—— 确认：地理地图为中心

拿到了一张真实产品截图（来自官方博客配图，非营销插画）：世界地图铺满整个画面（灰白色陆地+浅蓝海洋，
标注了"South Atlantic Ocean""Indian Ocean"等海域名），地图上散布着彩色圆形徽章，徽章里是数字（如"7""4""5""8"），
应为该区域聚合的在途/异常票数（粉色徽章疑似"迟到"聚合，紫色疑似另一类）。地图左上角**浮着两张卡片**（不是嵌入
地图图层，是盖在地图上的独立 UI 卡片）：第一张是"Welcome, Margaret"个性化问候+"36 active shipments"大字号
headline+分段进度条（颜色对应 Early/On time/Pending/Late 计数：Early 11、On time 0、Pending 0、Late 25）；
第二张是"p44 insights"标签+AI 生成的洞察标题"Red Sea crisis: shipments on vessels rerouting to avoid Red Sea"
+分页器"1 of 7"+两个操作按钮"See impact"和"Go to list →"。
【官方-真实截图】截图直链：https://www.project44.com/wp-content/uploads/2024/05/product-movement-disruption-map.jpeg
（来源博客：https://www.project44.com/blog/project44-enhances-movement-platform-with-new-capabilities-to-help-customers-mitigate-supply-chain-disruption/）

官方平台页文案佐证：Movement 分四层——Connect/See/Act/Automate，"See"层强调"追踪货物、抢在延误之前"；
落地页反复出现"map"关键词，用户可"缩放地图看单票位置，点进 shipment 详情页"。【官方，文案摘要，未逐句核验像素级细节】
https://www.project44.com/platform/ ｜ https://www.project44.com/

补充信号：project44 连续 5 年被 Gartner RTTVP（实时运输可视化平台）魔力象限评为 Leader（"Ability to Execute"
维度得分领先），是本次调研范围内业界公认的头部标杆之一。【官方】
https://www.prnewswire.com/news-releases/project44-named-a-leader-in-the-2025-gartner-magic-quadrant-for-real-time-transportation-visibility-platforms-for-fifth-consecutive-year-302387679.html

**结论：中央=世界地图（含聚合计数徽章），KPI 摘要与 AI 洞察以左上角浮层卡片堆叠形式叠加，不与地图同层绘制。**

### 2. FourKites（Intelligent Control Tower）—— 部分确认：地图存在，但中央形态未能锁定 + 明确的高管/运营分层

没能抓到 FourKites 完整控制塔桌面截图（多次尝试营销页/博客/新闻稿/YouTube 缩略图均未命中真实全屏截图，
只拿到风格化营销拼贴图：真人照片+浮层小卡片，如"ON TIME by 2d 14h 55m"状态徽章卡、"SHIPMENT DELAYED"+
"Reschedule delivery appointment"操作按钮卡、"ON TIME DELIVERY 33% (8,901)"+甜甜圈图占比卡）。
【官方-插画/营销拼贴，非完整截图】
https://www.fourkites.com/wp-content/uploads/real-time-visibility-platform-ui.png ｜
https://www.fourkites.com/wp-content/uploads/shipment-delayed-real-time-visibility.png ｜
https://www.fourkites.com/wp-content/uploads/advanced-analytics-rttvp.png

但确认了两条硬信息：
（1）**地图确实存在**——官方提到"Live Network Congestion Map"（追踪州际运输、全球港口延误、跨境货运）、
"Global Map View"（独立子站 map.fourkites.com）、"users can check load status... view loads on a map"。
【官方】https://www.fourkites.com/platform/real-time-visibility/ ｜ map.fourkites.com（子站，未能核实具体渲染内容）

（2）**高管/运营分层是明确产品化的**——"My Workspace"是一个独立的可定制仪表盘，官方原话："a single, unified
view... from operational users to the C-suite"，其"executive insights"板块专门展示"completed shipments,
orders, appointments and time slots"这类**历史趋势**供高管"monitor trends over time... report to stakeholders"，
区别于运营人员日常用的实时追踪视图。另有独立的"Executive Dashboard"提法，"provides key information in one
place to prioritize exceptions"。【官方】
https://www.fourkites.com/press/fourkites-launches-my-workspace-to-empower-customers-with-instant-customizable-supply-chain-insights/

品牌视觉语言值得一提（不代表产品 UI，但代表厂商想传达的隐喻）：FourKites 的营销插画反复用**字面意义上的机场
"空中交通管制塔"**造型（带雷达/天线的塔状建筑），塔周围悬浮告警卡片（"STOCKOUT RISK""CONTAINER DELAYED"等）。
【官方-插画】https://www.fourkites.com/wp-content/uploads/ICT-LP-Hero-Banner.png

**结论：地图是平台能力的一部分（且有独立的"全球地图视图"子产品），但未能核实是否为控制塔主屏的中央/唯一视图；
高管与运营用不同的仪表盘（My Workspace 执行摘要 vs 实时追踪）是被官方明确产品化的两套体验。**

### 3. Flexport（Control Tower）—— 中等证据：表格/列表为主面板，KPI 卡片+简化里程碑图为辅

官方 YouTube 频道发布的 Control Tower 介绍视频封面（非正片截屏，但是官方设计的产品风格化示意图，可信度高于
第三方猜测）显示：左侧主面板是一张**表格**（标题"Orders"，带筛选下拉框、多列、勾选框——典型数据表格布局，
不是地图）；右侧是一个简化的**线性里程碑示意图**（飞机图标→集装箱图标→目的地，中间一条虚线连接——这是
"货物进度条"不是地理地图）；下方两张卡片："Performance Summary"（几个百分比甜甜圈仪表）+一个柱状图。
【官方-插画/视频封面，非真实截屏，但为官方设计语言】
截图直链：https://img.youtube.com/vi/psp5KPnecU8/maxresdefault.jpg （视频：https://www.youtube.com/watch?v=psp5KPnecU8）

官方文案确认了**按角色/职能拆分的独立仪表盘**："new dashboards including Carrier Performance, Shipment
Execution, and Allocation Management"，供应商记分卡（Supplier Scorecards）是订单管理模块的一部分。
【官方】https://www.flexport.com/technology/control-tower/

**结论：与 project44/Shippeo 不同，Flexport 的视觉重心更像"运营台账"（表格为主）而非"地理可视化"，
地图/位置信息被简化为线性进度条；同样是"按职能拆分独立仪表盘"而非一张万能屏幕。**

### 4. Shippeo（Predictive Visibility）—— 确认：地图为中心 + 顶部 KPI 带 + 独立告警区

官方产品页的截图配图说明文字（图片本身无法直接查看，但官方为每张截图配了描述性 alt 文本，属于官方自述）显示
多个真实使用场景：一张"西班牙西北部地图+配送路线+路况信息"的仪表盘截图；一张"巴黎地图+配送路线，途经 4 个
Shippeo 仓库，含状态更新和时间戳"的截图；一张"北美地图（右侧）+左侧时间线导航"的截图，同一屏幕上还显示
"1 shipment with tracking issues, 1090 deliveries planned today, 687 deliveries planned today"这类汇总数字
——**位置在地图上方**，符合"KPI 带在顶部"的布局。告警作为独立分区展示，示例为"typhoon impact at Nagoya port"
（台风影响名古屋港）这类事件卡片。【官方，图片 alt 文本自述，未能肉眼核验像素排版但文字描述具体可信】
https://www.shippeo.com/en/platform/predictive-visibility

Shippeo 连续第 2 年被 Gartner RTTVP 评为 Leader。【官方】
https://www.businesswire.com/news/home/20250227846230/en/Shippeo-Named-a-Leader-in-the-2025-Gartner-Magic-Quadrant-for-Real-Time-Transportation-Visibility-Platforms-for-Second-Consecutive-Year

**结论：中央=地图（可以是区域级/城市级，不只是世界级），KPI 汇总数字在地图上方，告警是独立分区，
左侧还有时间线导航条——是本次调研里"地图+顶部 KPI 带"范式证据最扎实的样本。**

### 5. Infor Nexus（Control Center）—— 弱证据：确认 role-based views + 点击可执行动作，中央形态查不到

官方文案确认"role-based views, delivering tailored information for each user's specific needs"（角色专属
视图，非泛泛而谈——是产品能力清单里的正式条目）；"embedded operational analytic dashboards...click on
transactional data to quickly take action"（可以直接点数据行触发动作，不只是查看）。【官方】
https://www.infor.com/solutions/scm/infor-nexus/control-tower

未能确认中央视图具体是地图/表格/KPI 墙——多次尝试官网抓取均未拿到可用截图或更具体的版式描述。

### 6. E2open —— 弱证据：仅确认"跨产品统一体验"策略，中央形态查不到

E2open 的横向策略是"Harmony"——一套跨全部 E2open 产品线的统一 UX 层，官方原话"a common user experience
across all e2open's offerings...based on extensive user-based research"。【官方】
https://www.e2open.com/solutions/harmony-user-experience

多次尝试抓取控制塔具体页面/博客均未拿到界面版式的具体描述或截图（详见"查不到"节）。

### 7. Blue Yonder（Luminate Control Tower / Supply Chain Command Center）—— 弱证据：中央形态查不到

官方与第三方页面反复强调"机器学习读取多源数据、标记问题、推荐行动、what-if 情景比较"这类能力描述，
但没有一处具体描述屏幕版式。多次尝试（官网直连、G2、consulting 伙伴页、PDF solution sheet 下载）均未获得
可用截图。

### 8. SAP（Integrated Business Planning Supply Chain Control Tower）—— 确认：地图是可切换的一个组件（tile），非强制唯一中心

从 SAP 咨询伙伴博客里挖到了**两张真实产品截图**（教程配图，展示"如何增强地图细节"前后对比）：截图显示一个
标题为"Map"的**面板/tile**，右上角有一排图标——看形状分别是"卡片/列表视图切换""表格/网格视图切换""全屏展开"，
说明这张地图**是仪表盘里可以和表格视图互相切换的一个组件**，不是被焊死的全屏中央视图；底图为 HERE Maps
（右下角版权水印"Tiles Courtesy of HERE Maps"），世界地图范围，显示国界/城市点/城市名标注。
【第三方-真实截图（技术上是 SAP 官方产品，经咨询伙伴博客转载配图）】
截图直链：https://d2etvs2i9udppr.cloudfront.net/uploads/images/_bannerDesktopWebp/578129/Blog-To-This.webp
（来源：https://archlynk.com/blog/sap-ibp-control-tower-intelligent-visibility-enhancing-the-intelligent-visibility-map-in-two-simple-steps）

多条独立信息源交叉确认这个地图组件的官方名字叫"Intelligent Visibility"，明确是"geographic view of product
networks with search and alert visibility...visualize alerts, supply network lanes"（供应网络航线+告警叠加
在地理地图上）。【官方转述，多条第三方检索摘要互证】搜索摘要综合，未逐句核验单一原文。

SAP 社区里有一条真实用户支持贴"Issue with IBP Control Tower map and layers"，标题本身印证了"地图分图层
（layers）"这个功能设计确实存在且用户在实际使用（而非营销概念）。【官方社区，用户生成内容，间接印证】
https://community.sap.com/t5/supply-chain-management-q-a/issue-with-ibp-control-tower-map-and-layers/qaq-p/12780655

其余能力据官方与咨询伙伴描述：Analytics/Dashboards/Alerts & Exception Handling 三大板块，告警触发后可进入
"Case Management"做协同处理（人工介入的协作流程，不是自动化黑箱）。【官方，文案摘要】
https://help.sap.com/docs/SUPPORT_CONTENT/sibp/3354621926.html ｜ https://www.alphachain.eu/sap-ibp/supply-chain-control-tower/

**结论：SAP 是本次调研里唯一明确证实"地图只是仪表盘里众多 tile 之一、可以整块切换成表格视图"的产品——
与 project44/Shippeo 那种"地图铺满整个屏幕当骨架"的做法有本质区别。**

### 9. Altana（Atlas）—— 确认：知识图谱 + 地理地图的混合体，不是二选一

官方与第三方（含微软客户案例）多条信息交叉确认：Altana Atlas 的核心是一个"动态知识图谱"（dynamic knowledge
graph），节点是公司、设施、货运、贸易航线，边是所有权/交易/控制关系；**但这些节点同时"被定位在地图上"**——
官方原话"automatically map...multi-tier supply chain networks...locate them on a map and track the flows
of goods between them"。也就是说 Altana 不是纯抽象力导向图（force-directed graph，节点位置由算法摆放、
不代表地理坐标），而是**图的节点锚定在真实地理坐标上**——是"地图"与"关系图"两种范式的真正融合，而非本项目
Panorama.tsx 那种"把对象类型硬分层、层内聚合网格布局、层间画粗光路"的做法（后者的节点位置既不是地理坐标也不是
关系图布局算法结果，是纯粹为了排布好看而设计的网格）。【官方，多条来源综合，图谱+地图坐标绑定这一细节获两条
独立信源互证】
https://altana.ai/platform ｜ https://www.microsoft.com/en/customers/story/1661001566655499258-altana-prodyna-azure-en

**结论：Altana 提供了"图论视角"与"地理视角"如何融合的一个真实先例——节点=真实地理坐标锚定的实体，
边=真实业务关系，而非本项目当前"层=对象类型、位置=网格排版"的做法。**

### 10. Kinaxis（Maestro / RapidResponse，控制塔模块）—— 确认：不用地图，用"电子表格式工作表"

这是本次调研里"明确不用地图"证据最扎实的样本，且是"计划指挥类"对照组的代表。多篇第三方评测/评论综合确认：
"界面看起来很像在用 Excel 表格工作"（"a look similar to working in an Excel sheet"）；日常工作以"workbook"
（工作簿）和"worksheet"（工作表）为基本单位，官方文档原话"to view records in tables in RapidResponse,
worksheets need to be created"；控制塔模块本身用"Scorecards"（记分卡）聚合 KPI、标记超阈值的异常、支持
下钻到根因；用户反馈"至少有十几张日常要用的表，按钮多导航难，有学习曲线"。【第三方，多篇评测综合，含
softwareconnect.com 汇总的用户评价】
https://softwareconnect.com/reviews/kinaxis-maestro/ ｜ https://www.kinaxis.com/en/solutions/supply-chain-control-tower-and-visibility

官方控制塔页面文案本身（"real-time view of your entire ecosystem"）没有出现"map"这个词，反而强调
"what-if scenario"情景建模、"predict any future, from any past, for any horizon"——工作重心是**比较方案**
不是**看货在哪**。【官方】https://www.kinaxis.com/en/solutions/supply-chain-control-tower-and-visibility

**结论：Kinaxis 的控制塔是"表格/工作簿"骨架 + 记分卡聚合异常，服务于"计划与情景比较"这个核心任务，
不需要地理地图；这是与 project44/Shippeo 那类"可视性优先"产品最根本的范式差异，差异来自任务本身
（"货在哪"vs"该怎么调计划"），不是审美选择。**

### 11. o9 Solutions（Digital Brain / Control Tower）—— 弱证据：后端是知识图谱，UI 层中央形态查不到

官方文案确认底层数据模型是"Enterprise Knowledge Graph (EKG)"，控制塔基于此构建"数字孪生"，用于"sense
disruption early, understand its true business impact, and act decisively across planning and execution"。
【官方】https://o9solutions.com/digital-brain ｜ https://o9solutions.com/solutions/supply-chain-planning/supply-chain-control-tower

**这是后端数据模型层面的"图"，不代表 UI 层一定把图可视化出来给用户看**——多次尝试均未找到 o9 控制塔的
界面截图或版式描述，官网关键页面为 JS 渲染的单页应用，静态抓取拿不到内容。此项按"查不到"如实处理，
不能因为后端叫"知识图谱"就推断 UI 也画成图谱（规则 5：自信的具体≠真实，两者是不同层面的事）。

### 12. Anaplan（供应链规划应用）—— 确认：不用地图，网格/仪表盘为骨架，且新版 UX 明确移除了地图组件

与 Kinaxis 同属"计划类"对照组。Anaplan 官方产品页反复出现"intuitive dashboards and planning views"、
"grid"（网格模型）等词，不出现"map"。更直接的证据来自 Anaplan 官方用户社区一条真实帖子，标题就是
"Non availability of Map Chart in new UX"——用户在新版界面里明确找不到地图图表选项，说明（a）旧版 UX
可能有过地图图表选项，（b）新版 UX 主动移除/未提供。【第三方，用户社区真实反馈，非厂商自我宣传，可信度较高
但仅代表该功能在特定版本缺失，不代表 Anaplan 从未有过地图能力，如实标注局限性】
https://community.anaplan.com/discussion/67778/non-availability-of-map-chart-in-new-ux ｜
https://www.anaplan.com/solutions/supply-planning-software/

**结论：与 Kinaxis 一样，Anaplan 的核心任务是"数字模型规划"不是"位置追踪"，地图在这类产品里存在感很低，
甚至在产品演进中被边缘化。**

### 统计分布小结（12 个样本，按证据强度分层）

| 中央形态判定 | 产品 | 证据强度 |
|---|---|---|
| 地理地图为中心骨架 | project44、Shippeo | 中-强（各有真实/近真实截图或图注） |
| 地图是可切换的组件之一，非强制唯一中心 | SAP IBP Control Tower | 强（真实截图+图标证实可切换表格视图） |
| 知识图谱+地理坐标混合锚定 | Altana | 中（官方文字综合，无截图） |
| 表格/列表为主面板，位置信息简化为线性进度条 | Flexport | 中（官方视频封面，非真实截屏） |
| 明确不用地图，电子表格/工作簿为骨架 | Kinaxis | 中-强（多篇第三方评测综合） |
| 明确不用地图，网格/仪表盘为骨架 | Anaplan | 中（用户社区真实反馈） |
| 地图存在但中央形态未锁定 | FourKites | 弱（仅证实地图能力存在，未证实是否居中） |
| 查不到中央具体形态 | Infor Nexus、E2open、Blue Yonder、o9 | 无——如实空缺 |

**读数**：把"可视性优先"（project44/Shippeo/SAP 一角）和"计划优先"（Kinaxis/Anaplan）放在一起看，
中央形态的分野**跟着任务分野走**，不是审美选择——"货在哪"用地图，"怎么调计划/比哪个方案好"用表格。
即使在"可视性优先"阵营内部，也没有一家把地图当"唯一"的东西，KPI/告警/AI 建议永远是浮在地图上或贴在
地图边上的独立卡片层。

---

## 二、信息架构惯例（KPI 带 / 异常队列 / 地图列表配合 / 下钻入口）

- **KPI 摘要的位置有两种主流做法，样本里没有第三种**：(a) **顶部横带**——Shippeo 证实（汇总数字在地图上方）；
  (b) **浮层卡片堆叠在地图一角**——project44 证实（左上角两张卡片摞在地图上，不是横贯顶部的一条带）。
  两种做法的共同点：**KPI 摘要永远和地图分层绘制，从不混进地图图层本身**。【综合①②节证据】

- **异常/待办事项以独立卡片流或独立分区呈现，并且经常直接带操作入口**：project44 的"p44 insights"卡片自带
  "See impact"/"Go to list"两个按钮；Shippeo 有独立的港口级告警卡片（如台风影响）；FourKites 的"SHIPMENT
  DELAYED"卡片直接带"Reschedule delivery appointment"操作按钮（点开就能处理，不是只读通知）；SAP 的告警
  触发后可进入"Case Management"协同处理。**四个独立样本的共同模式：告警卡片不是纯展示，几乎都直接挂着
  下一步动作的入口**。【①②⑤⑧节证据综合】

- **地图与列表的配合**：Shippeo 截图里同一屏幕左侧是时间线导航条、右侧是地图；SAP 的地图组件可以整体切换成
  表格视图（同一份数据两种呈现，而非地图和表格同时常驻分屏）；Flexport 则反过来，表格是主面板、简化进度图
  是配角。**没有发现"地图和列表始终 50/50 分屏常驻"的样本**——要么主次分明（一个是骨架一个是配角），
  要么整体可切换（同一数据两种视图互斥展示，如 SAP）。【②④⑧节证据综合】

- **通用设计原则（非特定产品，行业从业者与设计文章的综合建议）**：一篇资深供应链投资人/从业者的实操文章
  给出明确的分层建议——KPI 要"少而且必须能对应到具体决策"（"Keep KPI sets tight and tied to decisions"），
  并且直接批评把控制塔首屏做成"一面数字墙"（"A control tower screen should not be a wall of numbers"）。
  【第三方-资深从业者观点】Benjamin Gordon（Cambridge Capital 创始人，专注物流/供应链科技领域投资 20 余年，
  3PLex 创始人后被 Maersk 收购）https://bengordonpalmbeach.com/a-step-by-step-guide-to-building-a-control-tower/

---

## 三、交互惯例（点击下钻出什么）

三个独立信源（一篇资深从业者文章 + 一份 SaaS UI 模式速查表 + 多个产品的实际证据）高度一致地指向同一条
标准下钻路径：

> **网络/地图总览 → 异常清单 → 单据（shipment/order）详情 → 可执行的动作**
> "Users should move from network overview, to exception list, to order or shipment detail, to the
> actions that can be executed."
> 【第三方-资深从业者观点】https://bengordonpalmbeach.com/a-step-by-step-guide-to-building-a-control-tower/

同一篇文章强调**下钻的视觉/交互模式必须在全产品内保持一致**，否则"训练成本上升、采用率下降"（"When
drilldowns feel different across screens, training time rises and adoption drops"）——这是一条纪律性建议：
不是"选侧滑还是选新页"本身有标准答案，而是"选了之后必须处处一致"。

具体到"弹出方式"，一份汇总多个 B2B/B2C SaaS 产品共性模式的速查表把控制塔类下钻归纳为两种主导模式：
**Modal/Drawer**（弹层或侧滑抽屉——"不离开当前上下文的情况下做聚焦任务/看详情"）和**Split Pane**
（分屏面板——"主从关系或编辑预览场景的并排双栏"）。【第三方】
https://gist.github.com/mpaiva-cc/d4ef3a652872cb5a91aa529db98d62dd

产品层面能直接印证"点击后触发操作而非仅仅是查看"的证据（见第一节详细描述，此处仅汇总指向"操作入口"这一
共性）：project44 的洞察卡片"Go to list"入口、FourKites 告警卡片"Reschedule delivery appointment"按钮、
Infor Nexus 官方原话"click on transactional data to quickly take action"、SAP 告警触发"Case Management"
协同流程。四个独立厂商样本的共同点：**点击不是终点，点击后大概率能直接触发一个动作**，这与业界建议的
"总览→异常→详情→动作"四段式路径吻合，动作是路径的终点而不是可有可无的附加项。

---

## 四、高管视角 vs 运营视角

这是本次调研里**分歧最小、共识最强**的一条：所有查到相关表述的信源，无一例外主张高管和运营必须是
两套不同的呈现，且都给出了具体理由，而不是空泛地说"要分角色"。

- **FourKites**：官方产品化为两个独立入口——"My Workspace"（含"executive insights"板块，看历史趋势、
  给董事会汇报用）与日常运营用的实时追踪视图分开。原话："from operational users to the C-suite"共用一个
  平台但不同呈现层。【官方，见第一节②】

- **Flexport**：按职能拆出独立仪表盘（Carrier Performance / Shipment Execution / Allocation Management），
  本质上是"运营职能视角"的横向切分，不是"高管 vs 运营"的纵向切分，但同样体现"没有一张万能屏幕"的思路。
  【官方，见第一节③】

- **Infor Nexus**：官方产品能力清单里明确写"role-based views, delivering tailored information for each
  user's specific needs"，作为正式功能条目（不是营销套话）。【官方，见第一节⑤】

- **一篇 Medium 上的控制塔仪表盘专题文章**（署名 C5i，一家数据分析/AI 咨询公司）给出了目前查到的**最具体
  的高管视角设计建议**：高管仪表盘应该"像飞行员的座舱——一眼看清系统状态，同时把需要干预的事情显眼地标出来"
  （"function like a pilot's cockpit"），能在**30 秒内看完**，同时给想深挖的人留下钻路径；具体展示内容
  应是"网络健康度（用热力图/红绿灯这类归纳性视觉）、对战略目标的达成情况、预测性风险、过滤过的异常摘要"，
  而不是运营人员看的原始指标。运营/规划/物流等不同职能岗位则各自需要完全不同的具体指标（需求计划员要
  "预测准确度、需求信号"，物流经理要"每英里成本、承运商表现"）。【第三方】
  https://medium.com/@c5i-ai/control-tower-dashboards-persona-based-insights-for-every-role-8347adf13166

- **Benjamin Gordon 的从业者文章**表述得更直接："Executives need risk and service exposure, operations
  leads need prioritized exceptions and throughput constraints, analysts need drill paths and
  root-cause signals"（高管要风险和履约暴露面，运营主管要排好优先级的异常和产能瓶颈，分析师要下钻路径和
  根因信号——三种角色，三种不同的信息，不是三种皮肤）；并且明确警告"Treating these layers as one screen
  is where many builds drift"（把这几层当成一张屏幕来做，是很多项目跑偏的地方）。【第三方-资深从业者观点】

- **AWS Supply Chain Command Center（SC3）**官方博客把"可视性"（Visibility：控制塔仪表盘、流程监控、
  告警）和"工作编排"（Work Orchestration：**按角色/群组划分的工作区**、任务分派与升级、审批流）列为两个
  并列但独立的产品模块，"Role and Group based workspaces"是官方正式列出的能力条目。【官方】
  https://aws.amazon.com/blogs/supply-chain/aws-supply-chain-command-center-for-resiliency-visibility-and-work-orchestration/
  （模块划分截图直链：https://d2908q01vomqb2.cloudfront.net/4d89d294cd4ca9f2ca57dc24a53ffb3ef5303122/2023/07/05/SCL-SC3-Modules-1.jpg）

**结论：没有查到任何一条反例（即"高管和运营共用一张屏幕"的正面案例）。** 唯一的分歧只在于"怎么分"——
按职能横切（Flexport）、按角色纵切（FourKites/AWS）、还是两者都做（Infor Nexus 笼统提及）；有没有分是
零分歧的共识，怎么分才是各家的差异化空间。

---

## 五、地图的取舍（对本项目关键）

这一节直接回应任务里的关键问题——本项目是"合成世界"，地理坐标是 UN/LOCODE 港口近似值，不是真实业务核心，
"要不要用地图"和"不用地图用什么"因此格外重要。

### 明确不用地图的产品，以及为什么

**Kinaxis** 和 **Anaplan** 是本次调研里"计划类"对照组的两个样本，都确认不以地图为中心（证据见第一节⑩⑫）。
两者的共同点是任务性质——它们的用户每天要做的事是"这个月的生产计划要不要因为这次中断调整""比较三个补货方案
哪个更优"，这些问题的答案跟"货物当前在地图上哪个点"关系不大，跟"数字模型算出来的结果"关系很大。Kinaxis 用
电子表格式的工作簿（worksheet/workbook）+ 记分卡（scorecard）承载这类任务；Anaplan 用网格模型
（grid）+ 仪表盘，新版 UX 甚至主动去掉了地图图表选项。

### 用地图但不是"唯一"或"强制常驻"的产品

**SAP IBP Control Tower** 是最有说服力的中间案例（证据见第一节⑧）：地图（"Intelligent Visibility"）被
实现成仪表盘里的一个**可切换组件**，同一份数据可以整体切成表格视图，说明厂商自己的产品设计判断是——
地图对"看供应网络航线上的告警"这类场景有用，但**不是所有场景都需要地图**，所以做成可选而不是焊死。

### 地图与关系图的第三条路：混合锚定

**Altana** 提供了一个不落入"地图 vs 抽象关系图"二选一陷阱的真实先例（证据见第一节⑨）：节点是有真实地理坐标
的实体（公司/设施/货运/航线），边是业务关系（所有权/交易/控制），两者同时存在——图论视角负责"看清楚谁连着
谁、风险怎么传导"，地理视角负责"看清楚东西在哪个国家/港口"，两者叠在同一张画布上而不是二选一。

### 对本项目的直接参考价值

本项目不是纯物流可视性工具（project44/Shippeo 那一类），而是覆盖钱/履约/客户/供应商/库存/AI/决策七个域
的全域控制塔——这个产品定位其实更接近"控制塔类产品里那些不完全依赖地图的部分"（SAP 的告警/KPI/Case
Management 板块、Kinaxis 的记分卡、AWS SC3 的角色工作区），而不是"以地图为绝对中心"的 project44/Shippeo。
换句话说，**业界证据本身并不支持"控制塔=必须是地图"这个隐含假设**——地图只在"这个产品的核心任务是追踪
物理位置"时才会被推到中心位置；当核心任务是"钱/客户/供应商/库存/AI/决策"这类没有统一物理坐标的东西时，
业界样本（Kinaxis/Anaplan/SAP 的非地图板块）反而更贴近参考对象。这条读数会在下一节的候选方案里具体展开。

---

## 查不到 / 未一手核验（如实列出，不脑补）

1. **FourKites 控制塔主屏的完整版式**——只确认地图能力存在（Live Network Congestion Map / Global Map View）
   与"My Workspace/Executive Dashboard"两套仪表盘的存在，但未能拿到完整截图确认地图是否为中央视图、
   KPI 带具体位置、异常队列具体位置。G2/Capterra 的截图页面均返回 403 拒绝抓取。
2. **Infor Nexus Control Center 的中央视图形态**——只确认"role-based views"和"点击可执行动作"两条能力，
   版式细节未找到可用截图或具体描述。
3. **E2open 控制塔中央视图形态**——多篇官方博客（"control tower capabilities re-imagined"等）均为战略/
   价值主张层面的文案，未提及具体版式；"Harmony"统一 UX 的实际视觉呈现未能核实。
4. **Blue Yonder Luminate Control Tower / Supply Chain Command Center 的中央视图形态**——多次尝试（官网、
   G2、consulting 伙伴页、官方 PDF solution sheet 下载）均未获得可用截图或具体版式描述；PDF 下载因 CDN
   URL 编码问题请求失败（HTTP 000，未重试更多次以控制调研时长）。
5. **o9 Control Tower 的 UI 层中央形态**——只确认后端数据模型为"Enterprise Knowledge Graph"，
   但这是数据层描述不是 UI 层描述，不能直接推断界面画的是图谱可视化；o9 官网关键页面为 JS 渲染单页应用，
   静态抓取拿不到实质内容。
6. **project44/FourKites/Flexport 等桌面端的精确下钻交互**（点击地图上一个点/一票 shipment 之后，是侧滑
   面板、新开页面、还是弹窗）——只有间接证据（project44 洞察卡片有"Go to list"按钮暗示跳转列表页，
   FourKites 告警卡片有"Reschedule"按钮暗示原地弹出可操作表单），没有拿到实际点击后的界面截图核实。
7. **Gartner《Magic Quadrant for Real-Time Transportation Visibility Platforms》原文细节**——报告本身在
   付费墙后（gartner.com/en/documents/5298863），本报告只使用了厂商自述"获评 Leader"及第三方新闻稿转述的
   评价维度（Ability to Execute / Completeness of Vision），未能核实报告原文对各厂商 UI/UX 的具体评价内容。
8. **Amazon 自营供应链的内部控制塔工具**——公开网络上只找到 AWS 对外销售的"Supply Chain Control Tower"/
   "SC3"产品资料，这是 AWS 面向企业客户的产品而非 Amazon 零售自用的内部工具截图，两者不能等同，本报告
   在引用时已注意区分措辞（"通过 AWS 产品资料侧面了解"而非"Amazon 内部工具長这样"）。
9. **Maersk 是否有一个统一的"驾驶舱"式主屏**——只确认 Maersk 对外提供多个垂直分工的独立产品（Visibility
   Studio / NeoNav 库存 / Customs Control Tower 关务），指向"多个专用控制塔而非一个统一驾驶舱"的结构，
   但未找到能证实或证伪"内部是否还有一个更高层的汇总驾驶舱"的资料。

---

## 对本项目七区信息的映射建议（三个候选形态，不做最终选择）

本项目驾驶舱体征带七区（`apps/api/cockpit.py`）：钱 / 履约 / 客户 / 供应商 / 库存 / AI 运营账 / 待拍板
（老板收件箱）。以下三个候选均有第一~五节证据支撑，各自的 trade-off 已尽量对齐真实业界样本而非主观臆造，
**最终选择留给主会话综合裁决**。

### 候选 A：地理地图为中心骨架（project44 / Shippeo 范式）

**做法**：中央铺一张世界/区域地图，用本项目已有的港口/仓库近似坐标（UN/LOCODE）做锚点；在途 shipment
用聚合计数徽章标注区域（呼应 project44 的"聚合徽章"而非逐票画点，避免 V9 决议已经诊断过的"实体级线条爆炸"
问题）；七区体征带改为左上角浮层卡片堆叠（呼应 project44）或顶部横带（呼应 Shippeo），待拍板/异常用独立
卡片流叠加在地图一角，参照 project44"insights 卡片带 Go to list 按钮"的做法直接挂下钻入口。

**Trade-off**：
- 优点：是业界"实时可视性"类控制塔的主流范式（project44/Shippeo/FourKites/SAP 均具备地图能力），直觉性强，
  本项目"合成世界"已有港口坐标可直接复用，不需要额外造数据；对"履约区"（在途货物）这个天然带地理属性的区
  最贴切。
- 缺点：本项目七个区里只有"履约"和部分"供应商/库存"（仓库/工厂位置）天然带地理坐标，"钱""客户满意度"
  "AI 运营账""待拍板"这几个区没有统一的地理位置可画——第五节已指出，业界证据本身也不支持"控制塔=必须是
  地图"，硬把无地理属性的区塞进地图隐喻，容易复刻"半个真控制塔"的问题（地图区独大，其余四个区被挤成
  侧边栏里的小字，本末倒置）。

### 候选 B：KPI/异常卡片墙为中心（Kinaxis / Anaplan / Benjamin Gordon 范式）

**做法**：中央直接放大现有的七区体征块，做成可点击的卡片墙（而非现状挤在顶部一条窄带），每区一张卡片
（headline 数字+趋势+告警计数），点击某区卡片下钻到该区的异常/工作队列列表（排序/优先级），再点具体条目
侧滑详情——完整复刻第三节总结的"总览→异常清单→详情→动作"四段式路径。地图作为可选的下钻终点之一
（比如从"履约区"卡片下钻两层后才看到地图），而不是首屏必需品。

**Trade-off**：
- 优点：七区天然映射为七张 KPI 卡片，不需要伪造或强行分配地理坐标；完全贴合 Benjamin Gordon"不该是一面
  数字墙但要角色化、KPI 要少而且对应决策"的建议，以及 Kinaxis/Anaplan"计划/决策类工具不需要地图"的先例
  ——本项目"待拍板/老板收件箱"这个区本质上就是决策工具，与 Kinaxis 的定位更接近；实现复杂度最低，直接
  在现有 vitals bar 基础上放大即可，不需要新的坐标系统或地图渲染管线。
- 缺点：业界"控制塔"品类（区别于纯计划类）的样本几乎都保留了某种空间感的可视化（哪怕不是地图，
  project44/Flexport 也有地图或进度条），纯 KPI 卡片墙如果做不好容易显得"不够控制塔感"、跟普通 BI 仪表盘
  区分度不足，视觉记忆点较弱。

### 候选 C：KPI 卡片墙为默认视图 + 履约区可整体切换为地图（SAP IBP 范式，混合但不强制）

**做法**：默认首屏是候选 B 的七区 KPI 卡片墙；其中"履约区"（唯一天然带地理属性、且现状已有港口/在途数据
支撑的区）卡片右上角提供一个视图切换入口（呼应 SAP"Map/Grid 切换图标"的做法），点击后该卡片/该区**整体**
切换成地图视图（复用候选 A 的地图设计），其余六区维持数字+趋势卡片不变。

**Trade-off**：
- 优点：不强迫全部七区信息挤进同一个隐喻，可视化投入集中在真正适合地图的履约区，其余区保持 KPI 墙的
  高信息密度和低实现成本；是本次调研里唯一有真实产品（SAP）验证过"tile 内切换"这个具体交互模式的方案，
  不是凭空设计。
- 缺点：三个候选里实现复杂度最高——需要同时维护两套视图状态（卡片态/地图态）及其切换逻辑，且切换入口如果
  做得不够显眼，容易出现"用户根本不知道能切换、地图功能等于没做"的情况（SAP 自己的社区支持贴"issue with
  control tower map and layers"侧面说明这类嵌套功能对用户确实存在认知门槛）。

---

## 附：调研中实际查看过的关键截图/图注来源一览（供后续核验）

- project44 真实产品截图（地图+浮层卡片）：https://www.project44.com/wp-content/uploads/2024/05/product-movement-disruption-map.jpeg
- SAP IBP Control Tower 真实产品截图（地图 tile，含视图切换图标）：https://d2etvs2i9udppr.cloudfront.net/uploads/images/_bannerDesktopWebp/578129/Blog-To-This.webp
- Flexport Control Tower 官方视频封面（表格+KPI 卡片布局）：https://img.youtube.com/vi/psp5KPnecU8/maxresdefault.jpg
- AWS SC3 模块划分图（Visibility vs Work Orchestration 五模块）：https://d2908q01vomqb2.cloudfront.net/4d89d294cd4ca9f2ca57dc24a53ffb3ef5303122/2023/07/05/SCL-SC3-Modules-1.jpg
- FourKites 品牌视觉语言（字面意义控制塔造型+告警卡片）：https://www.fourkites.com/wp-content/uploads/ICT-LP-Hero-Banner.png

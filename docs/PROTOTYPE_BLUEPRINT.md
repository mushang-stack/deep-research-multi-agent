# 原型蓝图 · 深度研究助手 v2.0(Figma)

> 对应 [PRD v2.0](PRD.md) 的 F1–F5 + 失败态 · 2026-08-25 · 目标:一天内产出可点击的中保真原型(桌面端 1440×900)

## 0. 今日产出物

1. 6 个 Frame(S1–S6,见下)组成的 Figma 文件
2. 可点击流程(Prototype 连线)
3. Notes 页(设计说明,面试讲决策用)

**设计语言**:白底 / 单一主色靛蓝 `#4F46E5` / 中文界面 / 中保真(不抠像素)。参考气质:Linear、Notion 的简洁 SaaS 风。

## 1. 信息架构与连线总表

```
S1 输入页 ──[开始研究]──▶ S2 运行中 ──(完成,自动)──▶ S3 报告页
                                                    │ [n] 引用角标
                                                    ▼
                                                 S4 溯源面板 ──[关闭]──▶ S3
顶栏[历史研究] ──▶ S5 历史库 ──(点某条)──▶ S3
S2 (失败分支) ──▶ S6 失败态(可选,时间不够可跳)
```

| 从 | 触发 | 到 | 交互类型 |
|---|---|---|---|
| S1 | 按钮「开始研究」 | S2 | Navigate to |
| S2 | (延时 5s,模拟完成) | S3 | After delay → Navigate |
| S3 | 引用角标 [1] | S4 | Navigate to(或 Open overlay) |
| S4 | 按钮「关闭」 | S3 | Navigate to(Back) |
| 全局顶栏 | 「历史研究」 | S5 | Navigate to |
| S5 | 列表某行 | S3 | Navigate to |
| S2 | 底部「查看错误」 | S6 | Navigate to(可选) |

## 2. 每屏蓝图

### S1 输入页(对应 F1)

| 分区 | 内容 |
|---|---|
| 顶栏 | 左:产品名「深度研究」;右:导航「新建研究 / 历史研究」 |
| Hero 中央 | 大标题「把一个问题,变成一份可溯源的研究报告」;副文「约 5 分钟,每条结论附来源与验证状态」 |
| 输入区 | 大输入框(placeholder:「输入研究问题,例如:对比 RAG 与微调的适用场景与工程权衡」);下方 3 个示例问题 chip,点击即填入 |
| 主按钮 | 「开始研究」(主色、醒目) |

### S2 运行中页(对应 F2,把 5 分钟等待变成可观察的过程)

| 分区 | 内容 |
|---|---|
| 顶栏 | 同 S1 + 当前问题文字 |
| 阶段进度条 | 横向 stepper:规划 → 并行检索 → 验证 → 撰写(当前步高亮 + 脉冲圆点) |
| 事件时间线(主区) | 滚动 feed,每行 = 图标 + 时间 + 文本。示意 6–8 条:「已拆解为 5 个子问题」「检索中:RAG 适用场景(3/5)」「已验证 12 条:supported 10 · weak 2」等 |
| 右侧遥测卡 | 已检索来源数 / 已耗时 / token 用量(数字示意) |

### S3 报告页(对应 F3 + F5 的回看)

| 分区 | 内容 |
|---|---|
| 顶栏 | 问题标题 + 状态徽章「✓ 完成」+ 按钮「导出」(置灰,标 P1) |
| 左侧 | 报告目录(4–5 节标题) |
| 主区 | 报告正文 2–3 段示意;**事实句尾带 [1][2] 引用角标(主色高亮,暗示可点)** |
| 右侧 | 引用来源列表:编号 + 标题 + 域名(与角标对应) |

### S4 溯源面板(对应 F4,核心差异化屏)

报告页之上滑出的右侧抽屉(画成独立 Frame 即可):

| 分区 | 内容 |
|---|---|
| 面板头 | 「结论溯源」+ 关闭按钮 |
| 结论区 | 被点开的报告原句(带 [1] 角标) |
| 来源卡 | 标题 / URL / **原文摘录**(灰色引用块) |
| 验证状态徽章 | 🟢 supported / 🟡 weak / 🔴 unsupported,三色徽章 |
| weak 提示行 | 「来源支撑较弱,建议人工复核」 |

**★ PRD 开放问题在此决策**:weak / unsupported 的结论**显式标注保留**,不静默删除。理由:产品差异化押「可信度」,静默删除 = 黑箱,标注 = 透明;unsupported 可折叠置底。做完原型后回填 PRD §7 一行即可。

### S5 历史研究库(对应 F5)

| 分区 | 内容 |
|---|---|
| 顶栏 | 同全局 |
| 列表 | 每行:问题 / 日期时间 / 状态(✓ 或 ✗)/ 来源数 / 「查看」;3–4 行示意 |
| 空态(可选) | 「暂无研究,去发起第一个 →」 |

### S6 失败态(可选,时间不够跳过)

S2 的变体:顶栏红色错误条「研究失败:检索源超时」;时间线末行 ✗;按钮「重试」「返回历史」。

## 3. Figma AI 提示词(First Draft / Figma Make 逐屏使用)

> 用英文提示词(生成质量更稳),中文界面文案在提示词里原样给出。逐屏生成,不要一次生成全部。

**S1:**
```
Clean professional web app landing screen for an AI research product, desktop 1440px, white background, single indigo (#4F46E5) accent, minimal Linear/Notion style, Chinese UI text. Top nav: product name "深度研究" on the left, links "新建研究" "历史研究" on the right. Centered hero: heading "把一个问题，变成一份可溯源的研究报告", subtext "约 5 分钟，每条结论附来源与验证状态", a large textarea with placeholder "输入研究问题，例如：对比 RAG 与微调的适用场景与工程权衡", 3 example-question chips below it, and a prominent primary button "开始研究".
```

**S2:**
```
Clean web app "research in progress" screen, desktop 1440px, white background, indigo accent, Chinese UI. Top nav with product name "深度研究" and a small line showing the current research question. Below it a horizontal 4-step progress stepper: "规划 → 并行检索 → 验证 → 撰写", current step highlighted with a pulsing dot. Main area: a scrolling vertical event timeline, each row = small icon + timestamp + Chinese text, examples: "已拆解为 5 个子问题", "检索中：RAG 适用场景（3/5）", "已验证 12 条：supported 10 · weak 2". Right sidebar: a telemetry card showing "已检索来源 9" "已耗时 2m 10s" "tokens 48k". Calm, observability-dashboard feel.
```

**S3:**
```
Clean web app report screen, desktop 1440px, white background, indigo accent, Chinese UI. Top bar: report title "对比 RAG 与微调的适用场景与工程权衡", a green badge "✓ 完成", a disabled "导出" button. Left sidebar: table of contents with 5 section titles. Main column: research report body text in Chinese, 2–3 paragraphs, where factual sentences end with small indigo citation markers [1] [2]. Right sidebar: numbered source list (title + domain), matching the citation numbers. Generous whitespace, reading-first layout.
```

**S4:**
```
A right-side sliding panel (drawer) over the report screen, desktop 1440px, white background, indigo accent, Chinese UI. Panel header "结论溯源" with a close ×. Inside: 1) the cited report sentence highlighted, ending with marker [1]; 2) a source card with title, URL, and a grey blockquote excerpt from the source; 3) a green verification badge "supported"; 4) below it a second card with an amber badge "weak" and a note line "来源支撑较弱，建议人工复核". Shows transparency and credibility.
```

**S5:**
```
Clean web app history list screen, desktop 1440px, white background, indigo accent, Chinese UI. Top nav same as other screens, section title "历史研究". A table/list of past researches, 4 rows, each row: question text, datetime, status icon (✓ green or ✗ red), source count, and a "查看" link. One row shows ✗ failed. Simple, information-dense but calm.
```

**S6(可选):**
```
Same as the research-in-progress screen but failed state: top red error banner "研究失败：检索源超时", the event timeline's last row shows a red ✗, and two buttons "重试" (primary) and "返回历史" (secondary). Chinese UI, white background.
```

## 4. 今日时间盒(约 4–5 小时)

| 时段 | 事 |
|---|---|
| 0:00–0:30 | Figma 注册 + 熟悉界面(Frame/文本/矩形/填充,官方 Figma for Beginners 前两节) |
| 0:30–2:00 | 逐屏跑 AI 提示词;每屏生成后对照 §2 蓝图检查缺漏,用文字追加修改 |
| 2:00–3:00 | 手动统一:字体字号/主色/间距,删 AI 多余元素;事件时间线做成组件复用 |
| 3:00–3:30 | Prototype 连线(§1 总表),Present 自查一遍 |
| 3:30–4:00 | Notes 页写设计说明(见下)+ 分享链接(Filename → Share → anyone with the link can view) |

**兜底**:若账号无 AI 功能或生成质量差 → Community 搜 "Untitled UI free" 复制组件库,照 §2 蓝图手动拼(每屏 30–40 分钟)。

## 5. Notes 页内容(PM 加分项,别省)

1. **信息架构图**(§1 抄过去)
2. **三个设计决策**:为什么过程透明是产品功能而非炫技(PRD 风险缓解);为什么溯源做成抽屉而非新页(不打断阅读流);为什么 weak/unsupported 标注保留(PRD 开放问题的决策 + 理由)
3. **指标挂钩**:S4 的引用角标 ↔ PRD「引用点开率 ≥ 30%」——原型里的每个差异化元素都对应一个指标

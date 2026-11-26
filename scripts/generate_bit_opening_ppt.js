#!/usr/bin/env node
"use strict";
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "docs/开题报告/北理工专硕");
const TITLE = "披露时点感知的A股短期尾部风险预测";
const NOTES = path.join(OUT, "开题答辩讲稿-邱琨原.md");
const W = 13.333333, H = 7.5;
const COLORS = {
  surface: "FCFCFB", card: "FFFFFF", ink: "0B0B0B", secondary: "52514E",
  line: "E1E0D9", soft: "F9F9F7", series: ["2A78D6", "EB6834", "1BAF7A"]
};
// The three categorical marks are fixed to S/E/G. Black direct labels and a
// legend provide the relief channel for the aqua mark's sub-3:1 contrast.
const SLIDES = [
  { title: TITLE, section: "两年制专业学位硕士｜学校版开题", kind: "cover", seconds: 20 },
  { title: "实践问题：同一公司的三种更新速度", section: "一 选题依据", kind: "timing", seconds: 35,
    items: ["收益每天更新；公告按事件出现；持股名单低频披露。", "报告期 ≠ 公开日 ≠ 数据库更新时间。", "问题：什么时候可用？陈旧信息最多能改动主干多少？"] },
  { title: "预测什么：未来五日的条件左尾", section: "一 选题依据 / 三 研究方案", kind: "label", seconds: 45 },
  { title: "近邻已有三路融合，区别须由实验回答", section: "一 选题依据", kind: "neighbors", seconds: 40 },
  { title: "研究内容：三路编码，辅助有界修正", section: "二 研究内容", kind: "architecture", seconds: 40 },
  { title: "公告抽取：先有金标，再冻结模型", section: "二 研究内容 / 三 研究方案", kind: "annotation", seconds: 45 },
  { title: "时点持股图：先认证名单与主体", section: "三 研究方案", kind: "bullets", seconds: 40,
    items: ["原始总股东1/1、流通2/2与补充记录分别处理。", "并列排名可能合法；不按相同排名只保留第一行。", "产品、管理人、托管分别核验；缺ID不猜合并。", "新完整名单按公开时点替换；退出Top10不等于清仓。"],
    banner: "当前主图未放行；以上是规则，不是全量图已认证。" },
  { title: "关系消息：返回目标前扣掉自己", section: "三 研究方案", kind: "graph", seconds: 55 },
  { title: "核心机制：限制辅助信息的最大修正", section: "三 研究方案", kind: "formula", seconds: 45 },
  { title: "单日装配：共享索引，不缓存随机表示", section: "三 研究方案", kind: "assembly", seconds: 35 },
  { title: "对照设计：比较算法，不比较信息权限", section: "三 研究方案", kind: "controls", seconds: 40 },
  { title: "四折时间外：验证选模，测试只评价", section: "三 研究方案", kind: "folds", seconds: 45 },
  { title: "指标与11消融：分开检验信息和机制", section: "三 研究方案", kind: "metrics", seconds: 40 },
  { title: "创新与边界：三个可检验贡献", section: "六 本课题创新之处", kind: "innovation", seconds: 35 },
  { title: "研究基础：已验底座，不是主实验结果", section: "七 研究基础", kind: "progress", seconds: 30 },
  { title: "关键缺口与资源门槛", section: "七 研究基础", kind: "gates", seconds: 35 },
  { title: "工作进度与预期成果", section: "四 进度安排 / 五 预期成果", kind: "schedule", seconds: 30 },
  { title: "总结：完成可追溯研究链", section: "总结", kind: "closing", seconds: 25 },
  { title: "备份：标签与统计追问", section: "答疑备份｜不计入主讲", kind: "qa-label", seconds: 0 },
  { title: "备份：名单与主体追问", section: "答疑备份｜不计入主讲", kind: "qa-graph", seconds: 0 },
  { title: "备份：LLM与创新追问", section: "答疑备份｜不计入主讲", kind: "qa-llm", seconds: 0 },
  { title: "备份：核心参考文献与来源", section: "答疑备份｜不计入主讲", kind: "references", seconds: 0 }
];

function notesByPage() {
  const chunks = fs.readFileSync(NOTES, "utf8").split(/^## (.+)$/m);
  const notes = new Map();
  for (let i = 1; i < chunks.length; i += 2) {
    const match = /^(\d{2}) (.+)$/.exec(chunks[i]);
    if (match) notes.set(Number(match[1]), { title: match[2], text: chunks[i + 1].trim() });
  }
  if (notes.size !== SLIDES.length) throw new Error("Speaker-note page count differs");
  SLIDES.forEach((slide, i) => {
    const note = notes.get(i + 1);
    if (!note || note.title !== slide.title || !note.text.includes(`建议用时：${slide.seconds}秒`)) {
      throw new Error(`Speaker notes or timing differ on page ${i + 1}`);
    }
  });
  return notes;
}

function validateSources() {
  const contract = JSON.parse(fs.readFileSync(path.join(ROOT, "project/config/experiment_protocol.json"), "utf8"));
  const source = fs.readFileSync(path.join(OUT, "开题报告-邱琨原.md"), "utf8");
  // This generator preserves the 2026-11-25 historical slide snapshot.
  const knownTitle = contract.title === TITLE || (contract.previous_titles || []).includes(TITLE);
  if (!knownTitle || contract.sequence_sessions !== 60 ||
      JSON.stringify(contract.seeds) !== "[42,2024,7,123,3407]") throw new Error("Locked protocol differs");
  for (const token of ["6,116,554", "3,607,689", "金标目前为0", "固定11项消融"]) {
    if (!source.includes(token)) throw new Error(`Source snapshot differs: ${token}`);
  }
  return contract;
}

function renderSlide(deck, spec, page, notes, geometry) {
  const slide = deck.addSlide();
  slide.background = { color: COLORS.surface };
  let serial = 0;
  function record(role, x, y, w, h, fontSize) {
    if (![x, y, w, h].every(Number.isFinite) || x < 0 || y < 0 || w < 0 || h < 0 ||
        x + w > W + 0.001 || y + h > H + 0.001) throw new Error(`Geometry overflow: ${page}/${role}`);
    const name = `p${page}-${role}-${++serial}`;
    geometry.push({ page, name, role, x, y, w, h, fontSize });
    return name;
  }
  function text(value, x, y, w, h, options = {}) {
    const fontSize = options.fontSize || 20;
    const name = record(options.role || "body", x, y, w, h, fontSize);
    slide.addText(value, { x, y, w, h, fontFace: "Microsoft YaHei", fontSize,
      color: COLORS.ink, margin: 0, breakLine: false, valign: "mid",
      ...options, objectName: name });
  }
  function shape(type, x, y, w, h, options = {}) {
    const name = record("shape", x, y, w, h);
    slide.addShape(type, { x, y, w, h, line: { color: COLORS.line, width: 0.8 },
      fill: { color: COLORS.card }, ...options, objectName: name });
  }
  function arrow(x, y, w) {
    shape(deck.ShapeType.line, x, y, w, 0, { line: { color: COLORS.secondary, width: 1.5, endArrowType: "triangle" } });
  }
  function bullets(items, x = 0.8, y = 1.85, w = 11.7, gap = 0.9, size = 20) {
    items.forEach((value, i) => text("• " + value, x, y + i * gap, w, 0.72, { fontSize: size, valign: "top" }));
  }
  function banner(value, y = 6.05) {
    shape(deck.ShapeType.roundRect, 0.7, y, 11.95, 0.55, { fill: { color: COLORS.soft } });
    text(value, 0.9, y + 0.07, 11.55, 0.4, { fontSize: 18, bold: true });
  }
  function card(title, lines, x, y, w, h, marker) {
    shape(deck.ShapeType.roundRect, x, y, w, h);
    if (marker !== undefined) shape(deck.ShapeType.ellipse, x + 0.18, y + 0.22, 0.13, 0.13,
      { fill: { color: COLORS.series[marker] }, line: { color: COLORS.surface, width: 1 } });
    text(title, x + (marker === undefined ? 0.2 : 0.4), y + 0.14,
      w - (marker === undefined ? 0.4 : 0.6), 0.65, { fontSize: 20, bold: true });
    text(lines.join("\n"), x + 0.2, y + 0.95, w - 0.4, h - 1.1,
      { fontSize: h < 2.2 ? 17 : 18, valign: "top", paraSpaceAfterPt: h < 2.2 ? 0 : 8 });
  }
  function table(headers, rows, widths, y, height = 0.74, size = 18) {
    [headers, ...rows].forEach((row, r) => {
      let x = 0.7;
      row.forEach((value, c) => {
        shape(deck.ShapeType.rect, x, y + r * height, widths[c], height,
          { fill: { color: r ? COLORS.card : COLORS.soft } });
        text(value, x + 0.13, y + r * height + 0.09, widths[c] - 0.26, height - 0.18,
          { fontSize: size, bold: r === 0 });
        x += widths[c];
      });
    });
  }
  function legend() {
    ["S 收益主干", "E 业绩预告", "G 持股关系"].forEach((label, i) => {
      const x = 0.85 + i * 3.65;
      shape(deck.ShapeType.ellipse, x, 6.15, 0.13, 0.13,
        { fill: { color: COLORS.series[i] }, line: { color: COLORS.surface, width: 1 } });
      text(label, x + 0.23, 6.06, 3.1, 0.32, { fontSize: 14, color: COLORS.secondary, role: "legend" });
    });
  }
  if (spec.kind !== "cover") {
    text(spec.title, 0.65, 0.45, 12.0, 0.65, { fontSize: 28, bold: true, role: "title" });
    text(spec.section, 0.68, 1.14, 11.9, 0.3, { fontSize: 13, color: COLORS.secondary, role: "section" });
    shape(deck.ShapeType.line, 0.65, 1.53, 12.0, 0, { line: { color: COLORS.line, width: 0.8 } });
  }

  switch (spec.kind) {
    case "cover":
      text("北京理工大学｜两年制专业学位硕士", 0.8, 0.85, 11.7, 0.5, { fontSize: 22 });
      text(TITLE, 0.8, 2.0, 11.6, 1.4, { fontSize: 34, bold: true, role: "title" });
      text("邱琨原", 0.85, 4.05, 5.0, 0.55, { fontSize: 26, bold: true });
      text("导师 / 类别领域 / 培养单位：待填写", 0.85, 4.85, 11.3, 0.45, { fontSize: 18 });
      text("编制：2026-11-25｜正式开题日待填写", 0.85, 5.55, 11.3, 0.4, { fontSize: 17, color: COLORS.secondary });
      text("依据附件8七栏配套制作，非学校官方PPT模板", 0.85, 6.25, 11.3, 0.35, { fontSize: 14, color: COLORS.secondary, role: "caption" });
      break;
    case "timing":
      bullets(spec.items, 0.8, 1.8, 11.7, 0.75);
      ["报告期 R", "公开日 P", "下一市场日生效", "预测日 t"].forEach((value, i) => {
        const x = 0.8 + i * 3.1;
        shape(deck.ShapeType.roundRect, x, 4.45, 2.55, 0.78);
        text(value, x + 0.1, 4.59, 2.35, 0.43, { fontSize: 18, align: "center" });
        if (i < 3) arrow(x + 2.55, 4.84, 0.55);
      });
      banner("仅示意处理顺序，不按图形距离编码时间，也不是实际公司案例。");
      break;
    case "label":
      table(["环节", "信息范围", "固定规则"], [
        ["起点回归", "截至 t 的历史", "252个有效观测，含截距三因子"],
        ["未来结果", "t 后五个市场日", "冻结 t 系数，累加因子残差"],
        ["历史阈值", "截至 t 已结束窗口", "252窗口：P10第26项 / P5第13项"],
        ["正类", "未来结果与历史比较", "严格小于阈值；未来缺观测则无标签"]
      ], [2.0, 3.25, 6.7], 1.85, 0.8, 18);
      banner("条件残差左尾 ≠ 原始收益必为负 ≠ 破产或因果传染。");
      break;
    case "neighbors":
      card("风险目标近邻", ["改造Graph WaveNet（2024）", "中国股票崩盘风险已有图时序研究。"], 0.75, 1.9, 5.85, 2.55);
      card("融合架构近邻", ["MSGCA（2025）", "指标、文本、图和门控回接已有先例。"], 6.8, 1.9, 5.8, 2.55);
      text("CN117808586A多关系图专利亦有重合；权利要求 / 部分全文待核。", 0.85, 4.85, 11.6, 0.66, { fontSize: 20 });
      banner("拟检验的是年龄有界修正，不把组件组合或主干回接称作首创。");
      break;
    case "architecture": {
      const rows = [
        ["S 收益主干", "60市场日 × 五主字段\nGRU64 → s128", "主干分数 z_s"],
        ["E 业绩预告", "Qwen3-8B QLoRA\n冻结缓存 → e128", "事件修正 a_e δ_e"],
        ["G 持股关系", "公开版本二部图\n去自身边龄消息 → g128", "关系修正 a_g δ_g"]
      ];
      rows.forEach((row, r) => row.forEach((value, c) => {
        const x = 0.8 + c * 4.15, y = 1.95 + r * 1.12;
        shape(deck.ShapeType.roundRect, x, y, 3.65, 0.87);
        if (c === 0) shape(deck.ShapeType.ellipse, x + 0.15, y + 0.35, 0.13, 0.13,
          { fill: { color: COLORS.series[r] }, line: { color: COLORS.surface, width: 1 } });
        text(value, x + (c === 0 ? 0.36 : 0.12), y + 0.1,
          c === 0 ? 3.17 : 3.41, 0.67, { fontSize: 18, align: c ? "center" : "left", bold: c === 0 });
        if (c < 2) arrow(x + 3.65, y + 0.44, 0.5);
      }));
      text("z = z_s + a_eδ_e + a_gδ_g", 1.0, 5.4, 11.3, 0.5, { fontSize: 25, bold: true, align: "center" });
      legend();
      text("主干包含共同诉讼结构控制；图示为设计，不代表各支路已训练。", 0.85, 6.45, 11.6, 0.22,
        { fontSize: 12, color: COLORS.secondary, role: "caption" });
      break;
    }
    case "annotation":
      card("固定1500篇公告", ["训练800 / 验证200 / 测试500", "方向 · 明确原因 · 逐字证据", "双标300为主样本子集"], 0.75, 1.95, 5.85, 3.6, 1);
      card("先试标，再冻结", ["A200 → B独立42", "冻结后：A余1300 / B余258", "原生字段 / 零样本 / QLoRA独立评价"], 6.8, 1.95, 5.8, 3.6);
      banner("当前人工金标为0；格式合格不等于语义正确，风险测试不读金标。");
      break;
    case "bullets":
      bullets(spec.items);
      banner(spec.banner);
      break;
    case "graph":
      card("公司 i：预测目标", ["当天历史表示 s_i", "返回前须扣除自身"], 0.75, 1.95, 3.45, 1.65);
      card("公司 k：其他共享公司", ["当天历史表示 s_k", "未来标签不作特征"], 0.75, 4.0, 3.45, 1.65);
      arrow(4.2, 2.75, 0.85); arrow(4.2, 4.75, 0.85);
      card("机构 j：加权总和", ["Σ w(k,j) φ(s_k)", "w = 持股比例 × 边龄衰减"], 5.05, 2.35, 3.05, 2.65);
      arrow(8.1, 3.67, 0.9);
      card("送回 i 的消息", ["分子 − w(i,j)φ(s_i)", "分母 − w(i,j)", "只剩其他公司贡献"], 9.0, 2.35, 3.6, 2.65);
      banner("规则示意。独享机构不提供关系；归一化边龄改变相对权重。");
      break;
    case "formula":
      text("a_m = M_m Q_m exp(−softplus(λ_m) A_m) sigmoid(G_m)", 0.85, 1.95, 11.55, 0.6, { fontSize: 24, bold: true });
      text("δ_m = tanh(辅助风险头)     z = z_s + a_eδ_e + a_gδ_g", 0.85, 2.83, 11.55, 0.6, { fontSize: 24 });
      text("M：是否有效   Q：可追溯质量   A：归一化年龄；softplus(λ) ≥ 0", 0.85, 3.53, 11.55, 0.26,
        { fontSize: 14, color: COLORS.secondary, role: "caption" });
      card("幅度上界", ["|a_mδ_m| ≤ M_mQ_m exp(−softplus(λ_m)A_m)", "陈旧度缩小允许的最大logit偏移。"], 0.75, 3.85, 6.3, 1.85);
      card("全缺失回退", ["M_e = M_g = 0 → z = z_s", "回到同一模型主干，不等于另训GRU。"], 7.25, 3.85, 5.35, 1.85);
      banner("结构性质不是泛化定理，也不是概率对年龄单调的保证。");
      break;
    case "assembly":
      ["共享公司日索引", "历史序列 / 事件缓存", "经认证版本查询", "当前权重联合计算"].forEach((value, i) => {
        const x = 0.75 + i * 3.1;
        shape(deck.ShapeType.roundRect, x, 2.05, 2.6, 0.9);
        text(value, x + 0.1, 2.18, 2.4, 0.58, { fontSize: 18, align: "center" });
        if (i < 3) arrow(x + 2.6, 2.5, 0.5);
      });
      bullets(["不物化600万份重复60日窗；每条去重事件只编码一次。", "缓存原始输入；GRU、投影、图消息按当前训练权重计算。", "逐来源检查 max_available_date ≤ 预测日；缺模态不删目标。"], 0.8, 3.5, 11.7, 0.75);
      banner("真实日装配仍待开发；不能以审计NPZ入口冒充全链路。");
      break;
    case "controls":
      table(["信息组", "模型", "输入与公平性"], [
        ["仅收益", "Logit / 强树 / GRU", "收益与因子；无辅助监督和诉讼"],
        ["同信息强树", "LightGBM / XGBoost / CatBoost", "同事件、正文PCA64、诉讼、图统计"],
        ["同编码器", "拼接 / 普通门控 / MSGCA式 / M", "同GRU、缓存与消息；主要改变融合"],
        ["图方向适配", "Graph WaveNet / 多关系启发式", "同任务重训；不冒称忠实复现"]
      ], [2.05, 4.65, 5.25], 1.9, 0.82, 17);
      banner("主对照：每折验证种子平均AP最佳同信息模型，排除M。");
      break;
    case "folds":
      table(["测试折", "训练", "验证", "测试"], [
        ["2021", "2017—2019", "2020", "2021"],
        ["2022", "2017—2020", "2021", "2022"],
        ["2023", "2017—2021", "2022", "2023"],
        ["2024部分年度", "2017—2022", "2023", "2024至07-24"]
      ], [2.7, 3.25, 2.5, 3.5], 1.85, 0.72, 19);
      text("固定种子：42 / 2024 / 7 / 123 / 3407；双方全部报告。", 0.85, 5.7, 11.6, 0.44, { fontSize: 19 });
      banner("结果结束日早于下一集合首个实际市场日；测试不调参。", 6.3);
      break;
    case "metrics":
      card("主评价与统计", ["AP；每日Top10%精确率 / 召回率", "各折各种子 → 种子均值 → 四折等权", "配对整日期20日块；5/60日敏感性"], 0.75, 1.95, 5.85, 3.55);
      shape(deck.ShapeType.roundRect, 6.8, 1.95, 5.8, 3.55);
      text("固定11项消融", 7.0, 2.09, 5.4, 0.65, { fontSize: 20, bold: true });
      text("1去公告 / 2去图\n3去年龄 / 4去质量\n5年龄仅作特征 / 6去辅助损失\n7仅去边级衰减 / 8随机重连图\n9仅原生 / 10仅Qwen结构\n11匿名文本", 7.0, 2.9, 5.4, 2.4,
        { fontSize: 17, paraSpaceAfterPt: 4, valign: "top" });
      banner("P5另作稳健性；CI跨0如实写未获得稳定增量。");
      break;
    case "innovation":
      card("1 年龄有界修正", ["约束最大logit偏移。", "缺失严格回退。", "性能增量待同信息实验。"], 0.75, 2.05, 3.8, 3.65);
      card("2 公平关系实现", ["公开版本 / 比例 / 边龄。", "目标自身不绕路返回。", "不称去自身图算子首创。"], 4.75, 2.05, 3.8, 3.65);
      card("3 可追溯工程", ["来源 / 版本 / 缓存 / 日装配。", "共同信息与时序统计。", "实践价值不替代算法创新。"], 8.75, 2.05, 3.8, 3.65);
      banner("近邻高度相关；创新表述由全文核验、机制与实证共同限定。");
      break;
    case "progress":
      table(["已验产物", "实际证据", "不能据此推出"], [
        ["标签 / 四折", "6,116,554标签；全量读回", "第二套算法全市场独立复算"],
        ["独立参照", "20公司 / 19,476标签一致", "全体公司算法完全正确"],
        ["新股东源", "3,607,689行 / 76字段 / CRC", "机构 / 名单 / 历史版本已认证"],
        ["工程原型", "77项合成功能回归通过", "真实金融预测性能提高"]
      ], [2.7, 4.6, 4.65], 1.85, 0.82, 18);
      banner("当前人工金标0、main_graph_ready=false；没有正式新模型分数。");
      break;
    case "gates":
      card("数据 Gate-D", ["原版 / 并列规则 / 更正。", "主体与历史证券对应。", "至少100组 / 100主体检查。"], 0.75, 1.95, 3.8, 3.45);
      card("金标 Gate-A", ["先200 / 独立42。", "规范冻结后余下标注。", "裁决与明确人工放行。"], 4.75, 1.95, 3.8, 3.45);
      card("设备 Gate-G", ["CPU接口 / 隔离 / 恢复。", "实测长度 / 显存 / 吞吐。", "24GiB仅为起始参考。"], 8.75, 1.95, 3.8, 3.45);
      banner("Gate-R最终检查：共同样本、全部种子、消融及数字来源。");
      break;
    case "schedule":
      table(["目标阶段", "工作", "产物"], [
        ["11月底", "学校材料 / 证据定位", "导师可审稿，不是考核已通过"],
        ["12月前期", "来源 / 金标 / 真实CPU接口", "数据和人工门槛放行"],
        ["门槛后至1月", "GPU小试跑 / 主比较 / 消融", "冻结结果与资源日志"],
        ["2月目标", "六章论文 / 复核 / 修改", "完整初稿及非敏感复现材料"]
      ], [2.4, 4.4, 5.15], 1.85, 0.82, 18);
      banner("研究目标日期不替代学院校历；前期未闭合，不启动正式训练。");
      break;
    case "closing":
      text("异步信息进入日频预测时，\n如何限制它对收益主干的修正？", 0.85, 2.0, 11.55, 1.3,
        { fontSize: 30, bold: true, align: "center" });
      bullets(["固定题目与完整主模型；不靠更换任务追求正结果。", "数据、金标、机制和时间外实验逐层形成证据。", "真实增量尚待检验；请各位老师批评指正。"], 1.0, 4.0, 11.2, 0.8, 21);
      break;
    case "qa-label":
      bullets(["条件尾部与普通下跌不同；未来因子只用于离线标签。", "整日期块保留标签重叠与横截面共同冲击。", "旧先导Logit AP 0.221784 / LightGBM 0.236579。", "约1.48个百分点是旧11特征表格比较，不是M提升。"], 0.8, 1.9, 11.7, 0.9, 20);
      banner("当前新协议下主模型未正式训练，不能给出预计AP。");
      break;
    case "qa-graph":
      bullets(["研究期严格结构候选129,500组，不等于全部可用名单。", "33,597组重复排名；大量持股数相同，需核合法并列。", "GDID / 名称多对多需身份核验，不自动模糊合并。", "退出Top10不证明清仓；未知辅助隔离但不删公司日。"], 0.8, 1.9, 11.7, 0.9, 20);
      banner("没有UpDt / 披露版本ID，历史覆写与原报告证据仍待取得。");
      break;
    case "qa-llm":
      bullets(["Qwen预训练晚于历史期：回顾性效用，不是当年部署复现。", "格式 / 逐字证据命中不等于语义正确；不使用自报置信度。", "约束可能牺牲有效信息，因此必须同编码器对照。", "负结果照实报告；毕业与投稿由真实工作及学院审查。"], 0.8, 1.9, 11.7, 0.9, 20);
      banner("匿名与原生字段控制减轻记忆风险，不证明绝无污染。");
      break;
    case "references":
      table(["综述编号", "方法依据", "可追溯入口"], [
        ["[1] / [4]", "条件偏度 / 三因子", "Chen等2001；Fama-French 1993"],
        ["[12]", "MSGCA最近融合架构", "10.1007/s40747-025-02023-3"],
        ["[17] / [22]", "GraphSAGE / QLoRA", "arXiv 1706.02216 / 2305.14314"],
        ["[24]", "EFSA事件级语义", "10.18653/v1/2024.acl-long.402"]
      ], [2.3, 3.85, 5.8], 1.85, 0.82, 18);
      banner("完整34篇见独立综述；引用数量不等于本人已完成30篇研读。");
      break;
    default: throw new Error(`Unknown slide form: ${spec.kind}`);
  }
  const source = spec.kind === "cover" ? "" :
    spec.kind.startsWith("qa") || spec.kind === "references" ? "来源：配套讲稿答疑；独立综述；CPU与股东补导验收。" :
      "来源：学校七栏开题稿；独立文献综述；2026-11-24数据与功能验收。";
  if (source) text(source, 0.7, 6.78, 11.85, 0.26, { fontSize: 11, color: COLORS.secondary, role: "source" });
  text("邱琨原｜专硕2年｜编制2026-11-25", 0.7, 7.18, 11.35, 0.2,
    { fontSize: 10, color: COLORS.secondary, role: "footer" });
  text(String(page).padStart(2, "0"), 12.22, 7.13, 0.43, 0.28,
    { fontSize: 11, bold: true, align: "right", role: "page" });
  slide.addNotes(notes.text);
  return slide;
}

async function main() {
  const args = process.argv.slice(2);
  function option(flag, fallback) {
    const index = args.indexOf(flag);
    if (index < 0) return fallback;
    if (!args[index + 1] || args[index + 1].startsWith("--")) throw new Error(`Value required: ${flag}`);
    return path.resolve(args[index + 1]);
  }
  const output = option("--output", path.join(OUT, "开题答辩-邱琨原.pptx"));
  const report = option("--report", null);
  if (fs.existsSync(output) && !args.includes("--overwrite")) throw new Error("Output exists; inspect it before --overwrite");
  validateSources();
  const notes = notesByPage();
  const deck = new pptxgen();
  deck.layout = "LAYOUT_WIDE";
  deck.author = "邱琨原"; deck.title = TITLE; deck.subject = "学校版开题配套展示；非官方PPT模板；正式评审未完成";
  deck.lang = "zh-CN";
  deck.theme = { headFontFace: "Microsoft YaHei", bodyFontFace: "Microsoft YaHei", lang: "zh-CN" };
  const geometry = [];
  SLIDES.forEach((spec, i) => renderSlide(deck, spec, i + 1, notes.get(i + 1), geometry));
  await deck.writeFile({ fileName: output });
  const result = { slide_count: SLIDES.length, main_slides: 18, backup_slides: 4,
    main_seconds: SLIDES.reduce((sum, item) => sum + item.seconds, 0),
    static_geometry_checked: true, geometry, palette: COLORS, source_snapshot: "2026-11-25",
    no_simulated_performance_chart: true, human_gold_count: 0, main_graph_ready: false };
  if (report) fs.writeFileSync(report, JSON.stringify(result, null, 2) + "\n");
  console.log(`Generated ${SLIDES.length} school slides: 18 main + 4 backup, ${result.main_seconds}s net speaking.`);
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { SLIDES, notesByPage, validateSources, renderSlide, COLORS, W, H };

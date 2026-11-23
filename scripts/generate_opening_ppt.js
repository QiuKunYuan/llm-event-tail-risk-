#!/usr/bin/env node
"use strict";

const pptxgen = require("pptxgenjs");
const path = require("path");

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "毕业设计开题汇报";
pptx.subject = "披露时点感知的A股短期尾部风险预测";
pptx.title = "披露时点感知的A股短期尾部风险预测";
pptx.company = "";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Microsoft YaHei",
  bodyFontFace: "Microsoft YaHei",
  lang: "zh-CN",
};
pptx.defineSlideMaster({
  title: "MASTER",
  background: { color: "F5F7FA" },
  objects: [
    { rect: { x: 0, y: 0, w: 13.333, h: 0.12, line: { color: "0C2340", transparency: 100 }, fill: { color: "0C2340" } } },
    { line: { x: 0.55, y: 7.12, w: 12.23, h: 0, line: { color: "DCE3EA", width: 0.8 } } },
    { text: { text: "硕士学位论文开题汇报  ·  研究方案工作稿", options: { x: 0.58, y: 7.18, w: 8.4, h: 0.16, fontFace: "Microsoft YaHei", fontSize: 8, color: "65758B", margin: 0 } } },
    { slideNumber: { x: 12.3, y: 7.17, w: 0.45, h: 0.17, color: "65758B", fontFace: "Microsoft YaHei", fontSize: 8 } },
  ],
  slideNumber: { x: 12.3, y: 7.17, w: 0.45, h: 0.17, color: "65758B", fontFace: "Microsoft YaHei", fontSize: 8 },
});

const C = {
  navy: "0C2340", blue: "2864A5", teal: "1C8C86", gold: "E3A53B",
  red: "C45656", ink: "172B43", muted: "52657A", pale: "EAF0F6",
  paleBlue: "E8F1FA", paleTeal: "E7F4F1", paleGold: "FBF2DF",
  white: "FFFFFF", line: "D8E0E8", gray: "75869A", green: "49876B",
};
const FONT = "Microsoft YaHei";
const S = pptx.ShapeType;
const W = 13.333, H = 7.5;

function slide(title, kicker = "研究设计") {
  const s = pptx.addSlide("MASTER");
  s.addText(kicker.toUpperCase(), { x: 0.62, y: 0.34, w: 4.8, h: 0.23, fontFace: FONT, fontSize: 9, bold: true, charSpacing: 1.4, color: C.teal, margin: 0 });
  s.addText(title, { x: 0.62, y: 0.7, w: 12.05, h: 0.5, fontFace: FONT, fontSize: 25, bold: true, color: C.navy, margin: 0, breakLine: false });
  return s;
}
function text(s, value, x, y, w, h, opts = {}) {
  s.addText(value, { x, y, w, h, fontFace: FONT, fontSize: 16, color: C.ink, margin: 0.02, breakLine: false, valign: "mid", fit: "shrink", paraSpaceAfterPt: 5, ...opts });
}
function card(s, x, y, w, h, heading, body, accent = C.blue, fill = C.white, fs = 14) {
  s.addShape(S.roundRect, { x, y, w, h, rectRadius: 0.12, line: { color: C.line, width: 0.9 }, fill: { color: fill }, radius: 0.1 });
  s.addShape(S.rect, { x, y, w: 0.09, h, line: { color: accent, transparency: 100 }, fill: { color: accent } });
  text(s, heading, x + 0.24, y + 0.14, w - 0.4, 0.28, { fontSize: 16, bold: true, color: accent });
  text(s, body, x + 0.24, y + 0.5, w - 0.42, h - 0.62, { fontSize: fs, valign: "top", breakLine: false, paraSpaceAfterPt: 7 });
}
function pill(s, label, x, y, w, color = C.teal, fill = C.paleTeal) {
  s.addShape(S.roundRect, { x, y, w, h: 0.34, rectRadius: 0.15, line: { color: fill, transparency: 100 }, fill: { color: fill } });
  text(s, label, x + 0.08, y + 0.01, w - 0.16, 0.3, { fontSize: 10, bold: true, color, align: "center" });
}
function arrow(s, x1, y1, x2, y2, color = C.gray, width = 1.4) {
  s.addShape(S.line, { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color, width, beginArrowType: "none", endArrowType: "triangle" } });
}
function note(s, value, x = 0.72, y = 6.62, w = 11.9) {
  text(s, value, x, y, w, 0.27, { fontSize: 10, color: C.muted, valign: "mid" });
}

// 1 Cover
{
  const s = pptx.addSlide();
  s.background = { color: C.navy };
  s.addShape(S.rect, { x: 8.9, y: 0, w: 4.43, h: 7.5, line: { color: "12385A", transparency: 100 }, fill: { color: "12385A" } });
  s.addShape(S.ellipse, { x: 9.3, y: 1.05, w: 2.75, h: 2.75, line: { color: C.teal, transparency: 30, width: 1.5 }, fill: { color: C.teal, transparency: 78 } });
  s.addShape(S.ellipse, { x: 10.35, y: 2.6, w: 1.65, h: 1.65, line: { color: C.gold, transparency: 20, width: 1.3 }, fill: { color: C.gold, transparency: 80 } });
  s.addShape(S.ellipse, { x: 9.05, y: 4.05, w: 1.3, h: 1.3, line: { color: "8EA8C2", transparency: 20, width: 1.2 }, fill: { color: "8EA8C2", transparency: 78 } });
  arrow(s, 10.25, 3.45, 10.78, 3.22, "8BA1B8", 1);
  arrow(s, 10.12, 3.7, 9.7, 4.3, "8BA1B8", 1);
  arrow(s, 10.2, 3.55, 10.75, 2.75, "8BA1B8", 1);
  text(s, "硕士学位论文开题报告", 0.85, 1.0, 6.9, 0.4, { fontSize: 17, color: "81D0C6", bold: true });
  text(s, "披露时点感知的\nA股短期尾部风险预测", 0.85, 1.8, 7.4, 1.65, { fontSize: 31, color: C.white, bold: true, breakLine: false, valign: "mid", lineSpacingMultiple: 1.15 });
  s.addShape(S.line, { x: 0.88, y: 3.82, w: 1.1, h: 0, line: { color: C.gold, width: 3 } });
  text(s, "公告事件  ·  收益时序  ·  机构持股图", 0.88, 4.12, 6.6, 0.42, { fontSize: 17, color: "D6E1EC" });
  text(s, "研究对象：公司自身未来五个交易日的条件异常下跌风险", 0.88, 4.75, 7.4, 0.6, { fontSize: 13, color: "ACBED0", breakLine: false });
  text(s, "汇报日期：2026年11月", 0.88, 6.53, 5.5, 0.25, { fontSize: 11, color: "ACBED0" });
}

// 2 task
{
  const s = slide("研究任务：每天收盘时，预测公司未来五日尾部风险", "问题定义");
  card(s, 0.75, 1.55, 3.6, 2.0, "预测时点", "交易日 t 收盘\n只使用 t 收盘前已公开的信息", C.blue, C.white, 16);
  card(s, 4.86, 1.55, 3.6, 2.0, "预测对象", "上市公司 i 自身\n不是预测新闻导致另一家公司下跌", C.teal, C.white, 16);
  card(s, 8.97, 1.55, 3.6, 2.0, "预测窗口", "之后 5 个市场交易日\n按交易日历，不按自然日", C.gold, C.white, 16);
  s.addShape(S.roundRect, { x: 1.25, y: 4.28, w: 10.85, h: 1.15, rectRadius: 0.1, line: { color: "D7E4EF", width: 0.8 }, fill: { color: "EAF1F7" } });
  text(s, "研究问题", 1.55, 4.5, 1.6, 0.28, { fontSize: 14, bold: true, color: C.blue });
  text(s, "公告事件 + 价格时序 + 持股关系，能否比量价模型更早、更稳地识别尾部样本？", 3.0, 4.45, 8.65, 0.48, { fontSize: 18, bold: true, color: C.navy });
  note(s, "研究解释预测关联，不将结果解释为因果关系或投资建议。");
}

// 3 label
{
  const s = slide("“尾部风险”如何落到可计算的标签？", "目标变量");
  card(s, 0.75, 1.5, 3.7, 2.08, "① 估计正常波动", "用预测日以前 252 个有效日，估计个股对市场三因子的敏感度。", C.blue, C.white, 15);
  card(s, 4.8, 1.5, 3.7, 2.08, "② 观察未来结果", "冻结预测日系数，计算后续 5 个交易日累计因子残差。", C.teal, C.white, 15);
  card(s, 8.85, 1.5, 3.7, 2.08, "③ 判定极端程度", "低于此前已结束窗口的公司自身历史 P10，标签记为 1。", C.gold, C.white, 15);
  s.addShape(S.roundRect, { x: 1.2, y: 4.03, w: 10.95, h: 1.12, rectRadius: 0.1, line: { color: C.line, width: 0.8 }, fill: { color: C.white } });
  text(s, "标签含义", 1.5, 4.24, 1.5, 0.26, { fontSize: 13, bold: true, color: C.teal });
  text(s, "相对于公司近期自身分布异常偏低的五日收益；它不等于破产、法定风险或必然负收益。", 3.0, 4.18, 8.6, 0.45, { fontSize: 15, color: C.ink });
  pill(s, "主标签 P10", 2.0, 5.55, 1.8, C.blue, C.paleBlue);
  pill(s, "稳健性 P5", 4.2, 5.55, 1.8, C.teal, C.paleTeal);
  pill(s, "窗口缺失则不造标签", 6.4, 5.55, 3.2, C.gray, "E8EDF2");
  note(s, "首轮标签面板：6,116,554 个公司日；2017-02-06 至 2024-07-24；P10 正类率 10.94%。");
}

// 4 data
{
  const s = slide("现有数据足以支撑固定研究主线", "数据基础");
  const data = [
    ["个股日收益", "909.2 万行", "2015–2024.07", C.blue],
    ["业绩预告", "16.2 万条", "主要公告文本", C.teal],
    ["诉讼仲裁", "2.1 万条", "辅助事件语料", C.gold],
    ["股东名单", "403.7 万行", "含信息发布日期", C.green],
    ["日三因子", "两套口径", "至 2025.12", C.blue],
    ["行业/状态历史", "约 7,364 条", "历史时点处理", C.teal],
  ];
  data.forEach((d, i) => {
    const x = 0.75 + (i % 3) * 4.1, y = 1.52 + Math.floor(i / 3) * 1.65;
    s.addShape(S.roundRect, { x, y, w: 3.68, h: 1.28, rectRadius: 0.1, line: { color: C.line, width: 0.8 }, fill: { color: C.white } });
    s.addShape(S.rect, { x, y, w: 0.08, h: 1.28, line: { color: d[3], transparency: 100 }, fill: { color: d[3] } });
    text(s, d[0], x + 0.25, y + 0.16, 3.1, 0.25, { fontSize: 13, bold: true, color: C.muted });
    text(s, d[1], x + 0.25, y + 0.48, 3.05, 0.34, { fontSize: 21, bold: true, color: C.navy });
    text(s, d[2], x + 0.25, y + 0.91, 3.1, 0.2, { fontSize: 10, color: C.gray });
  });
  s.addShape(S.roundRect, { x: 0.82, y: 5.07, w: 11.82, h: 0.92, rectRadius: 0.09, line: { color: "F0DEBB", width: 0.8 }, fill: { color: "FCF6E9" } });
  text(s, "数据限制", 1.1, 5.27, 1.2, 0.24, { fontSize: 13, bold: true, color: "9B6A17" });
  text(s, "未核实全期换手率、市值和财务发布日期；模型不依赖这些缺失字段。授权原始文件不上传 GitHub。", 2.35, 5.19, 9.9, 0.48, { fontSize: 13, color: C.ink });
}

// 5 literature
{
  const s = slide("同方向研究已有近邻：不把 LLM、图或门控本身称为原创", "文献定位");
  card(s, 0.78, 1.5, 3.75, 3.6, "风险目标 × 时序/多关系图", "Jing et al.（2024）使用 Graph WaveNet；中国专利 CN117808586A（2024）摘要披露时间序列＋多关系 GNN 预测股价崩盘风险。\n\n任务和方法骨架高度接近。需核验专利全文/权利要求，并把改造版 Graph WaveNet 纳入同标签对照。", C.blue, C.white, 12);
  card(s, 4.8, 1.5, 3.75, 3.6, "三模态预测 × 门控交叉注意力", "Zong et al.（2025），MSGCA\n\n指标序列＋动态文本＋关系图，使用门控交叉注意力。\n\n架构高度接近，列入直接模型基线。", C.teal, C.white, 14);
  card(s, 8.82, 1.5, 3.75, 3.6, "金融 LLM × 多关系动态融合", "Yi et al.（2026）\n\nLLM＋股票文本/价格＋多关系动态融合。\n\n标签和披露口径不同；仍需全文与中文数据库查重。", C.gold, C.white, 13);
  note(s, "已发现目标与方法骨架高度接近的中国专利，当前不能声称方向不重复。披露时点/边龄机制是否构成足够区别，须完成专利、学位论文查重并由导师确认。");
}

// 6 gap
{
  const s = slide("研究切口：按预测时点重建可用信息，检验五日尾部风险增量", "研究定位");
  const rows = [
    ["日收益", "每天变化", "GRU 编码最近 60 个交易日"],
    ["公告事件", "不定期发布", "只从公告公开后生效，聚合最近 20 日"],
    ["持股关系", "低频更新", "信息发布日期生效，边龄描述陈旧度"],
  ];
  rows.forEach((r, i) => {
    const y = 1.65 + i * 1.12;
    text(s, r[0], 1.05, y, 1.5, 0.3, { fontSize: 16, bold: true, color: [C.blue, C.teal, C.gold][i] });
    text(s, r[1], 3.0, y, 2.2, 0.3, { fontSize: 14, color: C.muted });
    arrow(s, 5.45, y + 0.16, 6.12, y + 0.16, C.line, 1.5);
    text(s, r[2], 6.4, y, 5.5, 0.34, { fontSize: 14, color: C.ink });
  });
  s.addShape(S.roundRect, { x: 1.0, y: 5.23, w: 11.3, h: 0.78, rectRadius: 0.1, line: { color: "D8E9E5", width: 0.9 }, fill: { color: "EDF6F3" } });
  text(s, "方法焦点", 1.3, 5.43, 1.4, 0.23, { fontSize: 13, bold: true, color: C.teal });
  text(s, "显式记录公告/持股何时可用、持股边有多旧；与 MSGCA 式普通门控直接比较。", 2.95, 5.35, 8.6, 0.4, { fontSize: 15, bold: true, color: C.navy });
}

// 7 hypotheses
{
  const s = slide("四个假设对应四个可比较的实验", "研究假设");
  const hs = [
    ["H1", "GRU 时序", "日收益序列能否超越无技能与表格参照？", C.blue],
    ["H2", "公告增量", "公告事件语义能否增加风险排序信息？", C.teal],
    ["H3", "图增量", "共同机构持股图能否提供额外关系信息？", C.gold],
    ["H4", "时点机制", "可用性/边龄条件融合是否优于 MSGCA 式普通门控？", C.green],
  ];
  hs.forEach((h, i) => {
    const y = 1.5 + i * 1.12;
    s.addShape(S.ellipse, { x: 0.95, y: y + 0.04, w: 0.52, h: 0.52, line: { color: h[3], transparency: 100 }, fill: { color: h[3] } });
    text(s, h[0], 1.0, y + 0.15, 0.42, 0.2, { fontSize: 12, bold: true, color: C.white, align: "center" });
    text(s, h[1], 1.8, y + 0.06, 2.3, 0.25, { fontSize: 16, bold: true, color: C.navy });
    text(s, h[2], 4.1, y + 0.04, 7.5, 0.34, { fontSize: 15, color: C.ink });
    if (i < 3) s.addShape(S.line, { x: 1.8, y: y + 0.78, w: 9.8, h: 0, line: { color: C.line, width: 0.6 } });
  });
  note(s, "每条假设均允许不成立；统计增益及其置信区间决定论文结论。");
}

// 8 architecture
{
  const s = slide("整体算法：三路编码 → 可用性感知门控 → 尾部风险概率", "核心模型");
  const bx = [0.8, 0.8, 0.8], by = [1.55, 3.12, 4.69];
  const labels = [
    ["收益序列", "60 日 GRU", C.blue, C.paleBlue],
    ["公告事件", "Qwen3-8B QLoRA + 时间聚合", C.teal, C.paleTeal],
    ["机构持股", "披露时点图 + GraphSAGE", C.gold, C.paleGold],
  ];
  labels.forEach((b, i) => {
    s.addShape(S.roundRect, { x: bx[i], y: by[i], w: 3.15, h: 1.06, rectRadius: 0.1, line: { color: b[2], width: 1 }, fill: { color: b[3] } });
    text(s, b[0], 1.02, by[i] + 0.18, 2.7, 0.25, { fontSize: 16, bold: true, color: b[2] });
    text(s, b[1], 1.02, by[i] + 0.55, 2.7, 0.28, { fontSize: 11, color: C.ink });
    arrow(s, 4.05, by[i] + 0.53, 5.3, 3.16, b[2], 1.35);
  });
  s.addShape(S.roundRect, { x: 5.35, y: 2.32, w: 3.3, h: 1.72, rectRadius: 0.1, line: { color: C.teal, width: 1.4 }, fill: { color: C.white } });
  text(s, "时点感知门控", 5.62, 2.62, 2.75, 0.32, { fontSize: 19, bold: true, color: C.navy, align: "center" });
  text(s, "可用标记 + 信息年龄", 5.62, 3.14, 2.75, 0.3, { fontSize: 13, color: C.teal, align: "center" });
  arrow(s, 8.65, 3.18, 9.55, 3.18, C.navy, 1.8);
  s.addShape(S.roundRect, { x: 9.62, y: 2.55, w: 2.65, h: 1.25, rectRadius: 0.1, line: { color: C.navy, width: 1.1 }, fill: { color: C.navy } });
  text(s, "二分类风险头", 9.88, 2.83, 2.1, 0.29, { fontSize: 17, bold: true, color: C.white, align: "center" });
  text(s, "未来 5 日尾部概率", 9.88, 3.23, 2.1, 0.22, { fontSize: 11, color: "D7E4EF", align: "center" });
  text(s, "只在预测时点可见的数据进入模型；标签在之后生成，仅用于训练和评估。", 2.2, 6.22, 9.0, 0.32, { fontSize: 13, color: C.muted, align: "center" });
}

// 9 event
{
  const s = slide("公告支路：语言模型负责抽取，不直接预测股票风险", "文本模块");
  const steps = [
    ["原始公告", "业绩预告文本", C.blue],
    ["人工金标", "约 1,500 条；20% 双标", C.teal],
    ["Qwen3-8B", "4-bit QLoRA 微调", C.gold],
    ["结构化事件", "方向、原因、证据", C.green],
  ];
  steps.forEach((v, i) => {
    const x = 0.65 + i * 3.18;
    s.addShape(S.roundRect, { x, y: 1.65, w: 2.45, h: 1.25, rectRadius: 0.1, line: { color: v[2], width: 1 }, fill: { color: C.white } });
    text(s, v[0], x + 0.12, 1.9, 2.2, 0.29, { fontSize: 16, bold: true, color: v[2], align: "center" });
    text(s, v[1], x + 0.12, 2.3, 2.2, 0.32, { fontSize: 11, color: C.ink, align: "center" });
    if (i < 3) arrow(s, x + 2.5, 2.28, x + 3.03, 2.28, C.gray, 1.4);
  });
  card(s, 1.0, 3.55, 5.1, 1.6, "抽取器评估", "分字段报告 Precision / Recall / F1、JSON 合法率、证据片段正确率和双人一致性。", C.teal, C.white, 14);
  card(s, 6.75, 3.55, 5.1, 1.6, "公司日事件输入", "预告按真实公开时间聚合；诉讼按公开日期计数。比较 RESSET 原生字段、8B零样本与QLoRA下游增量。", C.blue, C.white, 13);
  note(s, "已有约 16.2 万条业绩预告和约 2.1 万条诉讼仲裁；Qwen金标来自业绩预告，完整原文不进入公开仓库。");
}

// 10 graph
{
  const s = slide("股东图：关系只在真实公开之后生效", "关系网络");
  const companies = [[1.65,2.05,"公司 A"],[1.65,4.22,"公司 B"],[5.45,3.13,"公司 C"]];
  const insts = [[9.05,1.75,"机构 X"],[9.05,3.15,"机构 Y"],[9.05,4.55,"机构 Z"]];
  companies.forEach(([x,y,l])=>{
    s.addShape(S.ellipse,{x,y,w:1.25,h:0.72,line:{color:C.blue,width:1.1},fill:{color:C.paleBlue}});
    text(s,l,x+0.08,y+0.22,1.08,0.22,{fontSize:13,bold:true,color:C.blue,align:"center"});
  });
  insts.forEach(([x,y,l])=>{
    s.addShape(S.ellipse,{x,y,w:1.25,h:0.72,line:{color:C.gold,width:1.1},fill:{color:C.paleGold}});
    text(s,l,x+0.08,y+0.22,1.08,0.22,{fontSize:13,bold:true,color:"9B6A17",align:"center"});
  });
  [[2.9,2.35,9.05,2.1],[2.9,2.45,9.05,3.5],[2.9,4.45,9.05,3.5],[6.7,3.5,9.05,2.1],[6.7,3.6,9.05,4.9]].forEach(a=>arrow(s,...a,"9DADBD",1));
  card(s, 0.85, 5.62, 11.7, 0.65, "时点规则", "`InfoPubDt` 决定边生效日；报告期截止日不等于公开日。边记录持股比例和披露年龄。", C.teal, C.white, 12);
  note(s, "GraphSAGE 聚合公司—机构—公司邻居；不读取邻居未来标签、未来收益或之后披露的持仓。");
}

// 11 gate
{
  const s = slide("算法焦点：门控权重同时考虑内容、可用性和陈旧度", "融合机制");
  const blocks = [
    ["时序表示", "z_seq", C.blue, C.paleBlue],
    ["公告表示", "z_event", C.teal, C.paleTeal],
    ["关系表示", "z_graph", C.gold, C.paleGold],
  ];
  blocks.forEach((b,i)=>{
    const x=0.95+i*3.12;
    s.addShape(S.roundRect,{x,y:1.58,w:2.45,h:0.76,rectRadius:0.1,line:{color:b[2],width:1},fill:{color:b[3]}});
    text(s,b[0],x+0.1,1.76,2.2,0.25,{fontSize:14,bold:true,color:b[2],align:"center"});
    text(s,b[1],x+0.1,2.07,2.2,0.16,{fontSize:9,color:C.muted,align:"center"});
  });
  text(s, "+", 4.0, 2.74, 0.35, 0.25, { fontSize: 20, color: C.gray, bold: true, align: "center" });
  text(s, "各路可用状态 aₘ  +  信息年龄 qₘ", 4.5, 2.72, 5.3, 0.31, { fontSize: 15, bold: true, color: C.navy, align: "center" });
  arrow(s, 6.84, 3.16, 6.84, 3.65, C.teal, 1.5);
  s.addShape(S.roundRect,{x:3.65,y:3.72,w:6.35,h:0.72,rectRadius:0.1,line:{color:C.teal,width:1.2},fill:{color:C.white}});
  text(s,"门控网络 → softmax(g₁, g₂, g₃)",3.9,3.93,5.85,0.28,{fontSize:16,bold:true,color:C.teal,align:"center"});
  arrow(s,6.84,4.5,6.84,4.92,C.navy,1.5);
  text(s,"h = g₁z_seq + g₂z_event + g₃z_graph",2.5,5.02,8.7,0.35,{fontSize:19,bold:true,color:C.navy,align:"center"});
  note(s, "公告缺失时屏蔽事件分支；图很旧时门控可以降低其贡献。简单拼接和固定权重是必要对照。");
}

// 12 leakage controls
{
  const s = slide("信息时点与泄漏控制决定结果是否可信", "实验规范");
  const checks = [
    ["收盘时点", "预测日 t 收盘前已公开才能进入 X。"],
    ["公告生效", "没有可靠时分秒时，发布日期后的下一交易日生效。"],
    ["持股生效", "用 InfoPubDt 更新图；不按报告期末回填。"],
    ["未来收益", "只用于生成 y；不用于预测特征或邻居节点信息。"],
    ["标签边界", "训练/验证/测试间 purge 至少 5 个交易日。"],
    ["模型调参", "验证年选参数；锁定测试年仅评估，不择优回看。"],
  ];
  checks.forEach((p,i)=>{
    const col=i%2,row=Math.floor(i/2),x=0.82+col*6.05,y=1.52+row*1.35;
    s.addShape(S.roundRect,{x,y,w:5.65,h:1.02,rectRadius:0.1,line:{color:C.line,width:0.8},fill:{color:C.white}});
    s.addShape(S.ellipse,{x:x+0.18,y:y+0.22,w:0.35,h:0.35,line:{color:C.teal,transparency:100},fill:{color:C.teal}});
    text(s,"✓",x+0.205,y+0.265,0.3,0.2,{fontSize:12,bold:true,color:C.white,align:"center"});
    text(s,p[0],x+0.68,y+0.15,4.55,0.24,{fontSize:13,bold:true,color:C.navy});
    text(s,p[1],x+0.68,y+0.49,4.65,0.34,{fontSize:11,color:C.ink});
  });
  note(s, "五日标签窗口相互重叠，因此除 purge 外，还按连续交易日分块计算不确定性。");
}

// 13 protocol
{
  const s = slide("强基线比较：相同标签、公司日、时间折", "基线设计");
  const folds=[
    ["测试 2021", "训练 2017–2019", "验证 2020"],
    ["测试 2022", "训练 2017–2020", "验证 2021"],
    ["测试 2023", "训练 2017–2021", "验证 2022"],
    ["测试 2024*", "训练 2017–2022", "验证 2023"],
  ];
  folds.forEach((f,i)=>{
    const x=0.75+i*3.15;
    s.addShape(S.roundRect,{x,y:1.6,w:2.78,h:1.34,rectRadius:0.1,line:{color:C.line,width:0.8},fill:{color:C.white}});
    text(s,f[0],x+0.15,1.78,2.48,0.28,{fontSize:16,bold:true,color:C.navy,align:"center"});
    text(s,f[1],x+0.15,2.2,2.48,0.18,{fontSize:10,color:C.blue,align:"center"});
    text(s,f[2],x+0.15,2.53,2.48,0.18,{fontSize:10,color:C.teal,align:"center"});
  });
  text(s,"统一比较序列",0.92,3.55,2.0,0.27,{fontSize:14,bold:true,color:C.muted});
  const modelNames=["尾部Logit","LightGBM/XGBoost/CatBoost","GRU","Graph WaveNet","专利启发式多关系GNN","MSGCA","融合消融","时点年龄门控","本研究"];
  modelNames.forEach((n,i)=>pill(s,n,0.82+(i%3)*4.18,3.88+Math.floor(i/3)*0.57,3.45,i===8?C.white:C.blue,i===8?C.navy:C.paleBlue));
  note(s, "* 2024 测试只覆盖 1–7 月。验证年选超参数和最强基线；测试集不用于选赢家，所有模型复用同一测试公司日。专利启发式基线须以核验后的全文冻结实现。");
}

// 14 metrics
{
  const s = slide("主要回答“提高多少”：与验证期最强基线做配对 ΔAP", "评价方法");
  s.addShape(S.roundRect,{x:1.1,y:1.55,w:11.1,h:1.05,rectRadius:0.1,line:{color:"D5E7E2",width:1},fill:{color:"EEF6F3"}});
  text(s,"ΔAP = AP(本研究模型) − AP(验证期选定的最强基线)",1.45,1.86,10.4,0.34,{fontSize:19,bold:true,color:C.navy,align:"center"});
  card(s,0.9,3.0,3.6,1.58,"主指标：AP", "适合不平衡标签的正例排序评价。不能叫准确率。",C.teal,C.white,14);
  card(s,4.88,3.0,3.6,1.58,"风险名单", "Recall@Top10% + Precision@Top10%，说明有限审核容量的表现。",C.blue,C.white,14);
  card(s,8.86,3.0,3.6,1.58,"统计不确定性", "20 个交易日区块 bootstrap；另报 5 / 60 日区块敏感性。",C.gold,C.white,14);
  text(s,"若区间不支持稳定正增量，就报告“未观察到显著提升”。",1.5,5.35,10.2,0.34,{fontSize:17,bold:true,color:C.red,align:"center"});
  note(s,"同时报告 ROC-AUC、Brier、校准、逐年正类率与逐折 ΔAP；异论文分数不能代替同数据比较。");
}

// 15 pilot baseline
{
  const s = slide("先导量价基线：LightGBM AP 0.2366，尚不是完整强基线", "当前实证");
  const vals=[
    ["无技能参照",0.1072,C.gray],
    ["下行波动 Logistic",0.1307,C.gold],
    ["全特征 Logistic",0.2218,C.blue],
    ["LightGBM",0.2366,C.teal],
  ];
  vals.forEach((v,i)=>{
    const y=1.55+i*0.83;
    text(s,v[0],0.95,y+0.09,2.8,0.28,{fontSize:13,bold:i===3,color:C.ink});
    s.addShape(S.roundRect,{x:3.7,y,w:6.7*(v[1]/0.26),h:0.45,rectRadius:0.06,line:{color:v[2],transparency:100},fill:{color:v[2]}});
    text(s,v[1].toFixed(4),10.65,y+0.08,1.0,0.25,{fontSize:14,bold:true,color:v[2]});
  });
  s.addShape(S.roundRect,{x:1.0,y:5.2,w:11.2,h:0.88,rectRadius:0.09,line:{color:"F0DEBB",width:0.8},fill:{color:"FCF6E9"}});
  text(s,"边界",1.28,5.43,0.85,0.23,{fontSize:13,bold:true,color:"9B6A17"});
  text(s,"4 个测试期等权平均 AP；固定参数先导，无验证调参/区块区间；公告和图尚未接入。",2.28,5.34,9.4,0.39,{fontSize:12,color:C.ink});
  note(s,"CatBoost、XGBoost、Graph WaveNet、MSGCA 尚未在本地统一切分上运行；不把先导值写成最终结果。");
}

// 16 schedule
{
  const s = slide("从开题到 2 月初稿：四个月按交付推进", "进度安排");
  const items=[
    ["11/23–11/27","开题定稿","文献/专利查重、题目、PPT",C.blue],
    ["11/28–12/20","数据与强基线","时点审计、GBDT、GRU、Graph WaveNet",C.teal],
    ["12/21–01/04","公告模型","人工标注、QLoRA、独立测试",C.gold],
    ["01/05–01/18","股东图","实体映射、时点图、GraphSAGE",C.green],
    ["01/19–01/31","融合实验","专利式GNN、MSGCA对照、时点门控",C.blue],
    ["02/01–02/28","论文交付","2/7初稿、2月底修改稿",C.teal],
  ];
  items.forEach((v,i)=>{
    const col=i%3,row=Math.floor(i/3),x=0.8+col*4.13,y=1.55+row*2.15;
    s.addShape(S.roundRect,{x,y,w:3.65,h:1.65,rectRadius:0.1,line:{color:C.line,width:0.8},fill:{color:C.white}});
    pill(s,v[0],x+0.2,y+0.18,1.63,v[3],"EEF2F6");
    text(s,v[1],x+0.2,y+0.67,3.15,0.3,{fontSize:17,bold:true,color:v[3]});
    text(s,v[2],x+0.2,y+1.12,3.22,0.27,{fontSize:11,color:C.ink});
  });
  note(s,"时间紧：标注、图实体映射和文献核对并行；所有模型的训练/测试必须保持时间隔离。");
}

// 17 contribution / close
{
  const s = slide("预期贡献与答辩时应坚持的结论边界", "结论框架");
  card(s,0.82,1.53,3.72,2.05,"预测目标贡献", "用公司日五日因子残差P10标签，区别于年度崩盘代理和一般涨跌方向。",C.teal,C.white,14);
  card(s,4.8,1.53,3.72,2.05,"时点流程贡献", "按公告和持股真实公开时间构造可用信息，并表示图边信息年龄。",C.blue,C.white,14);
  card(s,8.78,1.53,3.72,2.05,"同任务实证", "同样本比较强树、Graph WaveNet、专利启发式多关系GNN、MSGCA式融合和本研究时点机制。",C.gold,C.white,12);
  s.addShape(S.roundRect,{x:1.0,y:4.15,w:11.35,h:1.15,rectRadius:0.1,line:{color:"EAD6D6",width:0.8},fill:{color:"FBEEEE"}});
  text(s,"不预先保证模型一定提升",1.35,4.4,4.5,0.33,{fontSize:18,bold:true,color:C.red});
  text(s,"门控、LLM和GNN已有近邻工作；只对实测且稳定的增量作结论，不能承诺模型一定提升。",5.2,4.31,6.6,0.52,{fontSize:13,color:C.ink});
  text(s,"所有结论以同样本、锁定的时间外结果为准。",2.25,5.82,8.8,0.34,{fontSize:17,bold:true,color:C.navy,align:"center"});
}

// 18 reference sources
{
  const s = slide("与本研究最相关的论文和 DOI", "参考文献");
  const refs = [
    ["Jing et al. (2024)", "China stock crash risk; Graph WaveNet", "DOI: 10.1016/j.frl.2024.105468", C.blue],
    ["Zong et al. (2025), MSGCA", "Stock movement; text + series + graph + gated fusion", "DOI: 10.1007/s40747-025-02023-3", C.teal],
    ["Yi et al. (2026)", "Financial LLM + multi-relational dynamic stock fusion", "DOI: 10.1007/s11280-026-01444-5", C.gold],
    ["Institutional networks × crash risk", "Hou & Liu (2023); Investor Network Density (2025)", "DOI: 10.1016/j.ribaf.2023.101906; 10.1111/ecpo.12348", C.green],
    ["Text × crash risk", "Peng et al. (2023); Banks’ Crash Text (2025)", "A-share annual reports and quarterly bank text; see literature review", C.blue],
    ["CN patent (2024)", "Crash risk + time series + multi-relation GNN", "CN117808586A; CN202311809825.3; full claims pending", C.red],
  ];
  refs.forEach((r,i)=>{
    const y=1.45+i*0.91;
    s.addShape(S.roundRect,{x:0.85,y,w:11.65,h:0.72,rectRadius:0.08,line:{color:C.line,width:0.7},fill:{color:C.white}});
    text(s,r[0],1.05,y+0.08,1.85,0.22,{fontSize:12,bold:true,color:r[3]});
    text(s,r[1],3.0,y+0.08,6.0,0.22,{fontSize:11,bold:true,color:C.navy});
    text(s,r[2],3.0,y+0.38,8.8,0.18,{fontSize:9,color:C.muted});
  });
  note(s,"完整来源、查重边界和基线协议见仓库《同方向文献核验与题目论证》；公开检索不等于穷尽查重。");
}

const out = path.resolve(__dirname, "../docs/开题报告/开题答辩汇报.pptx");
pptx.writeFile({ fileName: out })
  .then(() => process.stdout.write(`Wrote ${out}\n`))
  .catch((error) => { console.error(error); process.exitCode = 1; });

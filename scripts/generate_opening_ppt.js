#!/usr/bin/env node
"use strict";
const pptxgen = require("pptxgenjs");
const path = require("path");
const p = new pptxgen();
p.layout="LAYOUT_WIDE"; p.author="毕业设计"; p.title="披露时点感知的A股短期尾部风险预测";p.lang="zh-CN";
p.theme={headFontFace:"Microsoft YaHei",bodyFontFace:"Microsoft YaHei",lang:"zh-CN"};
const slides=[
["披露时点感知的A股短期尾部风险预测","硕士毕业设计开题汇报\n收益时序 · 公告事件 · 机构持股图\n固定方案与实验协议｜2026年11月"],
["研究任务","每天收盘，预测同一公司未来五个市场交易日的条件尾部标签\n输入：已知收益和三因子、已经生效的公告、已披露持股名单\n输出：风险分数和每日前10%预警名单\n标签衡量因子调整后的异常下跌，不等同于原始收益为负"],
["为什么研究披露时点","收益序列每天更新，公告间歇出现，持股名单低频披露\n报告截止日早于公开日，直接按报告期入图会产生未来泄漏\n旧名单与缺失事件不能当作持续有效的新信息\n需要区分缺失、抽取失败、信息陈旧和真实低风险"],
["数据基础","DRET：2015-01-05至2024-07-31，909.2万行\n已生成标签611.7万公司日，预测期至2024-07-24\n业绩预告16.2万条；诉讼仲裁2.1万条；股东记录403.7万行\n当前不把未核实的换手率、市值和财报字段写作必需输入"],
["标签定义","截至t用252个有效日观测拟合含截距的FF三因子回归\n冻结t的系数，累计未来五个市场交易日收益残差\n阈值：截至t已经结束的252个五日窗口，自身P10\nP5稳健性；未来任一交易日缺收益或因子则该标签无效"],
["有哪些同方向研究","Graph WaveNet：2024年中国股票崩盘风险预测\nCN117808586A：时序、多关系重构与融合GNN预测崩盘风险\nMSGCA：指标、动态文本、关系图的两阶段门控交叉注意力\nYi等2026：金融LLM与多关系动态融合股票趋势预测"],
["最近架构的重合与差异","已实际读取MSGCA全文IV-C/IV-D\n它已有完整指标引导文本和图、稳定门控和原指标回接\n本文不把三模态组合、收益主干或普通门控称为首创\n拟检验差异：实际披露年龄的修正上界、缺失回退与边级衰减"],
["完整模型路线","60日收益与因子 → GRU64 → 序列向量128 → 主干分数 z_s\n业绩预告 → Qwen3-8B QLoRA → 冻结缓存 → 事件向量128\n完整持股披露 → 公司—机构—公司加权消息 → 图向量128\n辅助分支按可用性、质量和年龄有界修正 z_s"],
["公告抽取与缓存","固定字段：经营业绩方向、明确原因、原文证据\n200条训练年份试标；1500条主标注；至少300条双标\n训练2017—2019、验证2020、独立测试2021—2024\n缓存正文token隐藏状态均值与预测字段，风险模型不读取测试金标"],
["公告质量怎样检验","严格原始JSON与清洗后可解析率分别报告\n方向Macro-F1、原因短语F1、证据精确匹配和原文支持率\n无事件与抽取失败分别记录，不能统一视为低风险\nQwen预训练晚于历史回测：匿名文本与原生字段控制，承认回顾性边界"],
["持股图如何避免假关系","按实际可用日生效，日期级披露延至下一市场交易日\n新完整Top10名单替换整份旧名单，消失股东不永久保留\nGDID优先；基金产品、管理人、名义托管人分开处理\n名单完整性、更新版本、覆盖率与边龄逐年审计"],
["边级年龄加权消息","边权 v = 持股比例 × exp(−softplus(λ_edge) × 边龄/252)\n公司序列向量 → 机构加权均值 → 公司加权聚合\n拼接公司自身序列表示，投影为128维图向量\n无邻居返回零图向量；采样邻居未来标签不参与训练"],
["核心算法：有界残差融合","a_m = M_m Q_m exp(−softplus(λ_m) A_m) sigmoid(G_m)\nδ_m = tanh(辅助风险头)，m为事件或图\nz = z_s + a_e δ_e + a_g δ_g；p = sigmoid(z)\n全缺失时z严格等于z_s；年龄增大时最大修正幅度上界缩小"],
["训练与实现","未加权BCE主损失 + 0.2序列辅助损失 + 各0.1事件/图辅助损失\nQwen先微调后冻结，GRU和图消息及融合门共同训练\nAdamW；验证AP早停；主模型5个随机种子全部报告\n原型已有张量接口、时点工具与评价代码；真实数据训练仍需人工金标和GPU"],
["怎样公平比较","仅收益：Logit、LightGBM/XGBoost/CatBoost、GRU\n同信息表格：收益 + 公告预测字段 + 诉讼 + 图统计 + 年龄\n同编码器：拼接、普通门控、MSGCA式适配、有界残差融合\nGraph WaveNet和专利启发式多关系模型明确标为任务适配"],
["如何测量创新作用","去事件/去图：信息是否提供增量\n去年龄、年龄只作普通特征：约束位置是否有效\n去质量、去辅助损失、仅去边衰减：机制拆解\n随机重连、匿名文本、原生字段控制：关系与文本可靠性"],
["正式时间外协议","训练2017—2019 / 验证2020 / 测试2021\n训练2017—2020 / 验证2021 / 测试2022\n训练2017—2021 / 验证2022 / 测试2023\n训练2017—2022 / 验证2023 / 测试2024至7月24；边界purge五日"],
["提高多少由什么数字回答","主指标AP；四折等权平均与逐年结果同时报告\nΔAP相对每折验证选出的最强同信息模型，测试不挑模型\n20交易日配对区块CI，5/60日敏感性；每日Top10精确率/召回率\n已有Logit 0.221784、LightGBM 0.236579；差1.48个百分点不是新模型提升"],
["实施计划与验收","11月：开题材料、时间协议与原型测试\n12月：数据/图版本审计、人工标注、正式强基线与GRU\n1月：Qwen微调、事件缓存、图模型与融合、四折消融\n2月7日前论文初稿，随后导师修改、复核、答辩与归档"],
["当前结论与待完成项","方案、算法、基线、测量方法已固定，可以按模块实施\n已有先导表格结果；新模型提升仍待真实训练确认\n专利权利要求及部分论文全文访问受限，不能写成查新已通过\n人工金标、点时版本核验、GPU训练和正式实验是后续必做验收项"]
];
slides.forEach(([title,body],i)=>{
 const s=p.addSlide(); s.background={color:i===0?"102840":"F5F7FA"};
 const ink=i===0?"FFFFFF":"17324D";
 s.addShape(p.ShapeType.rect,{x:0,y:0,w:13.333,h:.12,line:{color:"168C88"},fill:{color:"168C88"}});
 s.addText(title,{x:.65,y:.55,w:12,h:1,fontSize:i===0?30:27,bold:true,color:ink,margin:0,fit:"shrink"});
 if(i===7){
  const rows=[["60日收益与三因子","GRU64 → 向量128","序列主干 z_s"],["已生效公告正文","Qwen3-8B → 事件缓存","事件修正 a_e δ_e"],["完整持股披露版本","二部年龄加权消息","关系修正 a_g δ_g"]];
  rows.forEach((row,r)=>row.forEach((label,c)=>{
   const x=.8+c*4.1,y=1.85+r*1.1;
   s.addShape(p.ShapeType.roundRect,{x,y,w:3.6,h:.75,line:{color:"CEDBE5"},fill:{color:c===2?"DCEFEB":"FFFFFF"}});
   s.addText(label,{x:x+.12,y:y+.12,w:3.36,h:.5,fontSize:17,align:"center",color:ink,margin:0,fit:"shrink"});
   if(c<2)s.addShape(p.ShapeType.line,{x:x+3.6,y:y+.38,w:.5,h:0,line:{color:"168C88",width:2,endArrowType:"triangle"}});
  }));
  s.addText("风险分数 = sigmoid(z_s + a_e δ_e + a_g δ_g)",{x:1,y:5.55,w:11.2,h:.6,fontSize:24,bold:true,color:"168C88",align:"center",margin:0});
 }else s.addText(body,{x:.85,y:1.9,w:11.7,h:4.45,fontSize:21,color:ink,margin:0,breakLine:false,paraSpaceAfterPt:25,valign:"top",fit:"shrink"});
 s.addText("披露时点感知的A股短期尾部风险预测",{x:.65,y:7.12,w:10,h:.2,fontSize:9,color:i===0?"A5BACB":"697F90",margin:0});
 s.addText(String(i+1).padStart(2,"0"),{x:12,y:7.1,w:.6,h:.25,fontSize:10,color:"168C88",align:"right",margin:0});
});
p.writeFile({fileName:path.resolve(__dirname,"../docs/开题报告/开题答辩汇报.pptx")}).then(()=>console.log("Generated "+slides.length+" slides")).catch(e=>{console.error(e);process.exitCode=1;});

#!/usr/bin/env node
"use strict";
const pptxgen = require("pptxgenjs");
const path = require("path");
const p = new pptxgen();
p.layout="LAYOUT_WIDE"; p.author="毕业设计"; p.title="披露时点感知的A股短期尾部风险预测";p.lang="zh-CN";
p.theme={headFontFace:"Microsoft YaHei",bodyFontFace:"Microsoft YaHei",lang:"zh-CN"};
const slides=[
["披露时点感知的A股短期尾部风险预测","硕士毕业设计开题汇报\n收益时序 · 公告事件 · 机构持股图\n固定研究构成与实验协议｜数据验收更新2026年11月24日"],
["研究任务","每天收盘，预测同一公司未来五个市场交易日的条件尾部标签\n输入：已知收益和三因子、已生效公告、已公开完整持股名单\n输出：风险分数和每日前10%筛查名单，不作为买卖建议\n标签衡量因子调整后的左尾事件，不保证原始收益为负"],
["为什么研究披露时点","收益每天更新，公告稀疏出现，持股名单低频披露\n报告截止日不等于公开日；日期级披露延至下一市场交易日\n缺失、抽取失败、陈旧信息必须分别记录\n研究问题：怎样限制辅助信息修正，并验证这种限制是否有效"],
["真实数据底座","DRET：2015-01-05至2024-07-31，9,091,641行\n重建6,116,554标签与旧表一致；20公司19,476标签独立参照通过\n业绩预告162,168条；诉讼21,219条；股东4,036,775行\n没有经核实的价格、换手率、市值或财报时点必需字段"],
["股东审计改变了执行重点","已修复SHSer、UpDt真实表头别名，另存新版全量加工\n206,223候选组中189,735组有重复排名，约92.0%\n118,851组同排名身份字段冲突；不能任取十行\n持股比例已为0—1比值，不再除100；完整组/历史版本未认证"],
["标签定义","截至t用252个有效历史观测拟合含截距FF三因子回归\n冻结t的系数，累计未来五个市场交易日收益残差\n阈值用截至t已结束的252个五日窗口：P10取第26项\n各历史窗口冻结自身起点系数；P5取第13项，缺观测则无标签"],
["六层任务如何聚合为论文","底层字段与日期约束数据产品；编码器共享相同输入\n算法层提供可检验性质；实证层分别测信息与机制增量\n最后由冻结结果生成论文结论，不预写模型提升"],
["有哪些同方向研究","Graph WaveNet：2024年中国股票崩盘风险预测\nCN117808586A：时序、多关系重构与融合GNN预测崩盘风险\nMSGCA：指标、动态文本、关系图的两阶段门控交叉注意力\n近期金融LLM与多关系动态融合已有研究，不能称组合首创"],
["最近架构的重合与差异","已复查本地MSGCA arXiv v2方法IV-C/IV-D\n它已有完整指标引导文本和图、稳定门控与原指标回接\n拟检验：实际披露年龄对应的修正包络、全缺失回退和边级衰减\n专利权利要求、部分全文与学位论文待核；不是查新通过"],
["完整模型路线","60日收益与因子 → GRU64 → 序列向量128 → 主干分数z_s\n业绩预告 → Qwen3-8B QLoRA → 冻结缓存 → 学习事件投影\n完整持股披露 → 去自身二部年龄加权消息 → 图向量128\n辅助分支按可用性、质量和年龄有界修正z_s"],
["公告金标与缓存","固定字段：经营/财务方向、明确原因、原文证据\n1500主样本：训练800 / 验证200 / 测试500\n200试标属于训练800，300双标属于1500；当前金标为0\n冻结正文D维表示与预测字段；风险训练学习D→128，不读测试金标"],
["公告质量与历史边界","训练2017—2019、验证2020、独立测试2021—2024年7月\n独立报告方向Macro-F1、原因/证据指标、严格JSON与原文支持率\n完整序列2048token、正文起始预算1024；脚本整条跳过超长样本\nQwen预训练晚于回测：属回顾性研究，匿名控制不证明无污染"],
["持股图如何避免假关系","名单类型、来源、排名与修订证据共同认证完整组\n新完整名单替换旧整个列表，退出Top10不等于实际清仓\nGDID优先；产品与管理人分键，排除个人、名义托管与未知主体\n日期对齐不证明历史版本未覆写，须明确回顾性信息边界"],
["去自身的边龄加权消息","边权v = 总股本持股比例 × exp(−softplus(λ_edge) × 边龄/252)\n公司i经机构j接收消息时，从机构加权均值中减去i自身贡献\n独享机构不提供跨公司信息；无共享邻居时图可用性为0\n归一化衰减改变相对权重，不保证所有旧消息等比例缩小"],
["核心算法：有界残差融合","a_m = M_m Q_m exp(−softplus(λ_m) A_m) sigmoid(G_m)\nδ_m = tanh(辅助风险头)；z = z_s + a_eδ_e + a_gδ_g\n|a_mδ_m| ≤ M_mQ_m exp(−softplus(λ_m)A_m)\n全缺失回到同模型z_s；这是结构性质，不是泛化定理或概率单调性"],
["训练与已修复原型","完整模型：主BCE + 0.2序列辅助BCE + 各0.1事件/图辅助BCE\n仅收益GRU：只用主BCE，不接受公告/图辅助监督或诉讼控制\nQwen先训练后冻结；事件投影、GRU、图消息和融合共同训练\n34项测试通过，标签/四折索引已验；真实缓存、日装配器及部分对照待开发"],
["怎样公平比较","仅收益：Logit、LightGBM/XGBoost/CatBoost、GRU\n同信息表格：相同正文向量、预测字段、诉讼、图统计与邻居历史\n正文控制采用各折训练期拟合PCA64，验证/测试只transform\n同编码器拼接、普通门控、MSGCA式适配、M；直接图方向另作适配"],
["如何拆解创新作用","去事件/去图：信息增量；去年龄/年龄仅作特征：约束作用\n去质量/去辅助损失/仅去边衰减：定位具体机制\n随机重连/仅原生字段/仅Qwen结构字段/匿名文本：信息可靠性\n固定11消融；P5复核M、同信息主对照与普通门控，不选择最好结果"],
["正式时间外协议","训练2017—2019 / 验证2020 / 测试2021\n训练2017—2020 / 验证2021 / 测试2022\n训练2017—2021 / 验证2022 / 测试2023\n训练2017—2022 / 验证2023 / 测试2024至7月24\n共享索引全量读回已验；按outcome_end规则purge，不测试调参"],
["提高多少由什么数字回答","双方全部五种子：先每折每种子AP，再种子均值、四折等权\nΔAP相对每折验证选出的最强同信息对照，不以集成AP替代\n折内日期块配对CI：20日为主，5/60日敏感性；每日ceil(0.1N_t)\n先导Logit 0.221784、LightGBM 0.236579；差1.48个百分点不是M提升"],
["四个放行点，不作空泛保证","Gate-D：时点、完整名单、主体和历史版本有证据\nGate-A：1500裁决、300独立双标、年代与证据指标通过\nGate-G：真实GPU/IO吞吐和预算可排进时间，资源有余量\nGate-R：四折五种子、主对照、消融和数字来源完整；CI跨0如实报告"],
["资源与成本怎样核算","本机4CPU、约8.8GiB内存，无CUDA；不能训练Qwen QLoRA\n人工约1800次判定加裁决；先用200试标实测工时\n约420次神经/P5运行，另加192树候选上限与Qwen；预留30%余量\n24GiB GPU仅为起始配置；CPU合成步时不能外推正式GPU小时"],
["实施计划与关键路径","11月底—12月初：股东组认证与200试标，首先过数据门槛\n12月上旬：1500主标、双标和四折；中旬Qwen、缓存、真实计时\n12月下旬—1月：同信息强对照、四折五种子、固定消融和统计\n2月7日为初稿目标，不是核准学校截止；之后复核、修改和归档"],
["最终论文与投稿判断","六章：绪论 / 相关工作与定义 / 数据时点 / 方法 / 实验 / 总结\n证据由任务叶节点逐层聚合：金标、审计、模型、主表与消融\n硕士完整性来自闭合研究链；CCF-B还需近邻差异和真实稳定增量\n当前主图、金标、GPU与正式结果未完成，不承诺发表或预测提升"]
];
slides.forEach(([title,body],i)=>{
 const s=p.addSlide(); s.background={color:i===0?"102840":"F5F7FA"};
 const ink=i===0?"FFFFFF":"17324D";
 s.addShape(p.ShapeType.rect,{x:0,y:0,w:13.333,h:.12,line:{color:"168C88"},fill:{color:"168C88"}});
 s.addText(title,{x:.65,y:.55,w:12,h:1,fontSize:i===0?30:27,bold:true,color:ink,margin:0,fit:"shrink"});
 if(title==="完整模型路线"){
  const rows=[["60日收益与三因子","GRU64 → 向量128","序列主干 z_s"],["已生效公告正文","Qwen3-8B → 冻结缓存","事件修正 a_e δ_e"],["完整持股披露版本","去自身二部加权消息","关系修正 a_g δ_g"]];
  rows.forEach((row,r)=>row.forEach((label,c)=>{
   const x=.8+c*4.1,y=1.85+r*1.1;
   s.addShape(p.ShapeType.roundRect,{x,y,w:3.6,h:.75,line:{color:"CEDBE5"},fill:{color:c===2?"DCEFEB":"FFFFFF"}});
   s.addText(label,{x:x+.12,y:y+.12,w:3.36,h:.5,fontSize:17,align:"center",color:ink,margin:0,fit:"shrink"});
   if(c<2)s.addShape(p.ShapeType.line,{x:x+3.6,y:y+.38,w:.5,h:0,line:{color:"168C88",width:2,endArrowType:"triangle"}});
  }));
  s.addText("风险分数 = sigmoid(z_s + a_e δ_e + a_g δ_g)",{x:1,y:5.55,w:11.2,h:.6,fontSize:24,bold:true,color:"168C88",align:"center",margin:0});
 }else if(title==="六层任务如何聚合为论文"){
  const levels=["L0 可信底座\n字段·日历\n主体·版本","L1 数据产品\n标签·四折\n金标·披露图","L2 编码接口\nGRU·Qwen\n二部消息","L3 核心算法\n年龄包络\n性质·对照","L4 实证证据\n强基线\n消融·统计","L5 论文交付\n六章正文\n答辩·投稿"];
  levels.forEach((label,j)=>{
   const x=.65+j*2.04;
   s.addShape(p.ShapeType.roundRect,{x,y:2.05,w:1.85,h:1.65,line:{color:"CEDBE5"},fill:{color:j===5?"DCEFEB":"FFFFFF"}});
   s.addText(label,{x:x+.08,y:2.25,w:1.69,h:1.2,fontSize:16,align:"center",color:ink,margin:0,fit:"shrink"});
   if(j<5)s.addShape(p.ShapeType.line,{x:x+1.85,y:2.87,w:.19,h:0,line:{color:"168C88",width:2,endArrowType:"triangle"}});
  });
  s.addText(body,{x:.85,y:4.35,w:11.7,h:1.8,fontSize:21,color:ink,margin:0,paraSpaceAfterPt:18,fit:"shrink"});
 }else s.addText(body,{x:.85,y:1.9,w:11.7,h:4.45,fontSize:21,color:ink,margin:0,breakLine:false,paraSpaceAfterPt:25,valign:"top",fit:"shrink"});
 s.addText("披露时点感知的A股短期尾部风险预测",{x:.65,y:7.12,w:10,h:.2,fontSize:9,color:i===0?"A5BACB":"697F90",margin:0});
 s.addText(String(i+1).padStart(2,"0"),{x:12,y:7.1,w:.6,h:.25,fontSize:10,color:"168C88",align:"right",margin:0});
});
p.writeFile({fileName:path.resolve(__dirname,"../docs/开题报告/开题答辩汇报.pptx")}).then(()=>console.log("Generated "+slides.length+" slides")).catch(e=>{console.error(e);process.exitCode=1;});

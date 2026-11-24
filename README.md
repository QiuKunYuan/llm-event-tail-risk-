# A股短期尾部风险预测毕业设计

**题目：披露时点感知的A股短期尾部风险预测。**

每天收盘，使用已知收益、三因子、公告和机构持股关系，预测同一公司未来五个市场交易日的条件尾部标签。固定主模型为60日GRU、Qwen3-8B QLoRA、去自身的年龄加权二部GraphSAGE式消息和披露年龄约束的有界残差融合。全部模块属于主线，不列最低路线或扩展选项。

## 阅读与交付入口

1. [毕设最终构成与可行性论证](docs/开题方案/毕设构成与可行性论证.md)｜[Word](docs/开题方案/毕设构成与可行性论证.docx)：需求、六层树、算法复杂性、资源预算、论文六章与CCF-B证据门槛。
2. [完整研究设计](docs/开题方案/实施版研究设计.md)｜[Word](docs/开题方案/实施版研究设计.docx)：标签、公式、数据契约、对照和验收。
3. [详细开题正文](docs/开题报告/开题报告正文-确定方案.md)｜[Word](docs/开题报告/开题报告正文-确定方案.docx)｜[24页开题PPT](docs/开题报告/开题答辩汇报.pptx)。
4. [六层叶任务与逐文档审计](project/docs/毕业设计任务树与前期内容审计.md)｜[执行日程](project/docs/毕业设计周计划-2026-11至2027-02.md)｜[精简交接](docs/交接/毕设项目交接说明-2026-11-23.md)。
5. [同方向文献核验](docs/开题报告/同方向文献核验与题目论证.md)｜[首轮基线记录](docs/实验记录/首轮同样本基线比较.md)｜[字段审计](project/docs/数据字段映射与质量审计.md)。
6. **[数据补证与人工标注操作指南](docs/数据准备/数据补证与人工标注操作指南.md)｜[Word](docs/数据准备/数据补证与人工标注操作指南.docx)**：先标200、第二人独立42；股东字段字典/小样本/10组原版报告怎么补。实测见[标注与映射验收](docs/数据核查/标注工具与来源映射验收-2026-11-24.md)。

## 当前实际状态

- 2026-11-24已全量重建6,116,554标签，解压内容与旧表完全一致；独立NumPy参照核验20家公司、19,476标签，数值与P10/P5标签均无不一致。四折共享索引及全量读回验收已完成，详见[CPU数据验收](docs/数据核查/CPU数据验收-2026-11-24.md)。
- 先导11特征Logit平均AP=0.221784，LightGBM=0.236579。差约1.48个百分点是旧表格模型比较，不是新设计提升；旧Top10按全年混排，不是每日预警。
- 股东字段已修复并在仓库外重加工403.7万行。206,223候选组中189,735有重复排名，118,851组同排名身份字段冲突。比例已核为0—1比值，不再除100；完整名单、主体与历史版本未认证，`main_graph_ready=false`。
- 公司码解析已分离Comcd/CompanyCode；全量新旧比较仅DRET的4,318,444行公司码改变，其他原字段与股东旧18列不变。已生成观察crosswalk与10组/167原行私有补证任务，历史有效映射/完整图仍未认证，不自动补缺证券码。
- 中文Excel已生成并逐字读回：A200、B42、A余1300、B余258。800/200/500与300双标成员不变；草稿检错、独立比较、裁决/人工放行和SFT严格预检工具已验。**当前人工金标为0，空试标有效对0；本机无CUDA，按用户安排前期准备完成后才租GPU。真实Excel尚未私有传到本机。**
- **69项功能测试通过**：既有34项、新标注27项与映射8项；覆盖来源篡改/成员/字段/证据、独立原稿与Kappa退化、金标拒绝草稿、命名空间/旧字段保持等，不是金融提升证据。
- 正文缓存、共享索引真实日装配器、普通门控/MSGCA和图方向适配、正式强表格调参及矩阵调度仍待开发。现有训练入口消费审计NPZ，不是已完成RESSET全链路。

## 运行与再生成

先安装与设备匹配的PyTorch，再安装`requirements-research.txt`；表格基线、QLoRA分别见`requirements-baseline.txt`与`requirements-qlora.txt`。本地测试包目录为`/home/admin/workspace/.python-packages`，按需设置PYTHONPATH；正式CPU/GPU环境须分别冻结。

```bash
python3 -m unittest discover -s project/scripts -p 'test_*.py' -v
python3 project/scripts/train_research_model.py --manifest /path/to/audited_fold/manifest.json --output-dir /path/outside/repo/model --device cuda
python3 project/scripts/evaluate_predictions.py --input /path/to/frozen_paired.csv --seed-models m42 m2024 m7 m123 m3407 --seed-baselines b42 b2024 b7 b123 b3407
```

四折、种子与时点合同见`project/config/experiment_protocol.json`；当前不是所有脚本都由该文件驱动。主统计先各折各种子AP、再种子均值与四折等权，不能用概率集成AP替代。配对CI在折内抽整日期块；主对照只按验证表现选。

```bash
npm install
npm run generate:proposal
python3 scripts/export_proposal_docx.py
```

Word为Markdown的可编辑导出，公式保留源表示。包/XML验收不等于Office视觉验收；学院模板、公式与引用格式仍需终稿处理。

公开仓库只保存方法、代码和非敏感聚合。RESSET原始数据、公告正文、逐样本预测、模型权重、密钥均不得推送。已审阅后删除四个旧PNG/drawio；原始ZIP、唯一背景资料及仓库外原方案/参考文献保留。

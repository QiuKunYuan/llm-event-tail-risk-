# A股短期尾部风险预测毕业设计

**题目：披露时点感知的A股短期尾部风险预测。**

每天收盘，使用已知收益、三因子、公告和机构持股关系，预测同一公司未来五个市场交易日的条件尾部标签。固定主模型为GRU、Qwen3-8B QLoRA、年龄加权二部GraphSAGE式消息和披露年龄约束的有界残差融合。全部模块属于主线。

## 阅读与交付入口

- [完整研究设计报告](docs/开题方案/实施版研究设计.md)｜[可编辑Word稿](docs/开题方案/实施版研究设计.docx)：标签、算法公式、数据契约、训练与验收。
- [详细开题正文](docs/开题报告/开题报告正文-确定方案.md)｜[可编辑Word稿](docs/开题报告/开题报告正文-确定方案.docx)。
- [20页开题PPT](docs/开题报告/开题答辩汇报.pptx)。
- [同方向文献核验](docs/开题报告/同方向文献核验与题目论证.md)：MSGCA全文已读，部分全文与专利权利要求仍受访问限制。
- [任务树](project/docs/毕业设计任务树与前期内容审计.md)｜[日程](project/docs/毕业设计周计划-2026-11至2027-02.md)｜[交接](docs/交接/毕设项目交接说明-2026-11-23.md)。
- [首轮基线记录](docs/实验记录/首轮同样本基线比较.md)｜[数据字段审计](project/docs/数据字段映射与质量审计.md)。

## 当前实现与结果边界

标签面板6,116,554公司日，2017-02-06至2024-07-24；P10正类率10.94%。先导11特征Logit平均AP=0.221784，LightGBM=0.236579。差约1.48个百分点是已有表格模型的比较，不是新设计提升。

原型新增可运行的模型、时间规则、按折训练入口和每日Top10/区块CI评价。完整模型仍需人工金标、股东历史版本核验、RESSET日批次接入和GPU训练。相关方向已有文献，方法的新意以同信息对照和消融检验，不预设胜出。

## 运行

先安装与设备对应的PyTorch，再安装 requirements-research.txt；表格基线另见 requirements-baseline.txt，QLoRA依赖见 requirements-qlora.txt。

```bash
python3 -m unittest discover -s project/scripts -p 'test_*.py' -v
python3 project/scripts/train_research_model.py --manifest /path/to/audited_fold/manifest.json --output-dir /path/outside/repo/model --device cuda
python3 project/scripts/evaluate_predictions.py --input /path/to/paired_predictions.csv --model prediction --baseline baseline
```

训练输入是一日一份审计NPZ，契约见研究报告第14节。输出必须在仓库外。四折、种子和指标配置见 project/config/experiment_protocol.json。功能测试用合成数据，不用于金融效果结论。

```bash
npm install
npm run generate:proposal
python3 scripts/export_proposal_docx.py
```

Word为Markdown内容的可编辑导出，公式保留源表示；学院模板、公式排版和最终参考文献格式需按学校要求处理。PPT与Word均可从版本化源码重新生成。

RESSET原始数据、公告正文、逐样本预测、模型权重和密钥不得推送。已删除旧四因子整体方案、旧导师汇报和重复入口，避免与固定方案混用。

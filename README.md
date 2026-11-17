# llm-event-tail-risk

建议题目（待导师确认）：基于公告事件表征的A股短期尾部风险预测研究。

## 当前执行方案

当前主任务是预测公司自身未来 5 个交易日的条件异常下跌；研究结论限于样本外预测关联，不声称因果风险传染。实现方案、可用数据口径和实验顺序以[实施版研究设计](docs/开题方案/实施版研究设计.md)为准。

首轮先用公司历史收益和市场因子比较 Logistic 与 LightGBM，再逐项加入业绩预告结构化信息和公告语义；共同股东关系是可选扩展。QLoRA、GRU、GraphSAGE 和门控融合不是按期完成论文的前提。开题正文草稿见[这里](docs/开题报告/开题报告正文-初稿.md)。

## 目录结构

```
docs/
├── 开题方案/     执行方案与背景材料
├── 导师汇报/     选题汇报PPT和讲解稿
└── 数据核查/     RESSET字段核查、导出说明和列名审计工具
图片/             论文结构图和实施流程图
scripts/          可复用的数据审计与处理脚本
```

## 数据说明

RESSET 原始数据和处理结果保存在工作区外部数据目录，不入 Git。当前方案只使用已有的 DRET、日三因子、业绩预告、诉讼仲裁、股东、行业和状态文件；不要求补导新表。原始数据具有授权限制，不能上传公开仓库。

已基于 DRET 与日三因子生成首轮基线面板：`processed/tail_risk_labels.csv.gz`，包括历史收益/波动特征、未来五日尾部标签和生成口径统计。需要重建时运行 `python3 scripts/build_tail_risk_labels.py`；脚本使用系统自带的 `g++`、GNU `sort` 和 `gzip`，原始工作簿不会被修改。

首轮同样本基线比较（历史下行波动、Logistic、LightGBM）见[实验记录](docs/实验记录/首轮同样本基线比较.md)。复跑前安装 `requirements-baseline.txt`，然后运行 `python3 scripts/run_baseline_comparison.py --threads 4`。模型输出写入工作区外的 RESSET 处理数据目录，不会写入 Git。

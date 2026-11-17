# 毕设项目

本目录是毕设的执行入口，采用分阶段方式实现数据审计、日行情整理、尾部风险标签和预测模型。研究问题固定为：

> 用预测日收盘时已经可获得的信息，预测一家公司未来五个市场交易日自身的条件异常下跌风险。

当前主标签使用日三因子残差：截至预测日估计三因子系数并冻结，计算未来五个交易日残差和；与此前 252 个已经完成的五日结果比较，低于公司自身历史 P10 记为正类。P5 用于稳健性分析。研究结果解释为预测关系，不解释为公司间因果传染。

## 目录

- docs/学习路线与资源.md：按实现顺序安排学习内容、官方资料和 B 站检索入口。
- docs/数据现状与缺口.md：根据当前本地文件核对的已有数据与实际缺项。
- docs/数据字段映射与质量审计.md：RESSET 原字段映射、全表覆盖/缺失审计、时点合并规则。
- docs/标签和基线审计结论.md：标签口径与当前首轮时间外基线的审计结果。
- docs/毕业设计任务树与前期内容审计.md：从最终论文倒拆到可勾选的小任务，并记录前期材料的冲突、缺项和待配合事项。
- ../docs/开题报告/开题报告正文-初稿.md：按常见开题结构写成的可审阅正文；待迁入学校模板，并按导师反馈和文献核验结果修订。
- docs/公告人工标注规范-v1.md：业绩预告事件语义的标注字段与裁决规则。
- docs/公告模型微调运行说明.md：标注包、SFT 数据转换和租用 GPU 上的 QLoRA 命令。
- config/project.json：样本范围、标签定义和因子口径。
- scripts/audit_data.py：读取本地审计报告，重新检查日期覆盖、行情表和换手率字段。
- scripts/audit_processed_inputs.py：流式检查八张标准化数据表的字段缺失、日期覆盖、股票日重复、因子分组和股东候选池；结果写入 reports/input_quality.json。
- scripts/audit_label_panel.py：复核标签与阈值关系、行数/日期、基线特征清单和时间切分 purge；结果写入 reports/label_panel_audit.json。
- scripts/prepare_announcement_annotations.py：按年份和 RESSET 预测类型抽样，生成 300 条主标注和 60 条双标模板。
- scripts/build_sft_dataset.py：验证人工标注后，生成按年份切分的聊天格式 JSONL。
- scripts/train_qlora.py：在 CUDA 租用环境上用 4-bit NF4 和 LoRA 训练 Qwen3-1.7B。
- scripts/evaluate_event_extractor.py：在留出的人工时间测试集上评价 JSON 格式、方向、原因和证据抽取。
- scripts/normalize_daily_market.py：把 RESSET 导出的 CSV/CSV.GZ/单 CSV ZIP 转为统一字段的压缩 CSV。
- scripts/build_labels.py：调用仓库已有的 C++ 加速标签实现，并将新结果写入本项目 outputs。
- outputs/：生成的数据与报告，不提交 Git。

原始 RESSET 文件仍放在工作区的 RESSET论文数据目录，不复制到 Git 仓库。

## 当前运行顺序

从仓库根目录运行：

~~~bash
python3 project/scripts/audit_data.py
python3 project/scripts/audit_processed_inputs.py
python3 project/scripts/audit_label_panel.py
~~~

当前主线不要求补导 DRESSTK；只有导师要求扩展样本期或增加成交/换手/市值特征时，才运行行情整理。每段导出保存为一个文件：

~~~bash
python3 project/scripts/normalize_daily_market.py \
  --input /path/to/DRESSTK_2014_2015.csv \
  --output project/outputs/processed/daily_market.csv.gz
~~~

需要追加多个时段时，把多个输入文件同时传给 --input；程序按输入顺序流式写出，不会将数百万行一次性读入内存。排序、跨期去重和滚动特征会在后续面板步骤中处理。

需要重建当前标签面板时，先核对本地 DRET 和因子数据范围，再运行：

~~~bash
python3 project/scripts/build_labels.py
~~~

可通过 --data-root 指定 RESSET 数据目录，通过 --output 指定标签输出位置。重建标签前先核对收益与因子日期范围；当前本地标签只覆盖到 2024-07-24。

## 实现原则

1. 先审计，再合并；字段名相似不视为口径相同。
2. 日期以交易日为准，预测时点后的公告、财务和股东披露不得进入特征。
3. 标签窗口最后五个预测日需要后续五个市场交易日数据。
4. 先完成传统机器学习和严格时间外评估，再加入公告语义与共同持股特征。
5. 每个新增模块都与相同样本、相同时间切分的基线比较。


# 图文长版开题报告 LaTeX 工程

当前状态：源稿与图表定义已完成，正在安装编译依赖并进行PDF验收；尚未宣布正文30页达标。本文源稿含图表说明约2万个汉字，图表页数及版式以最终编译为准。

本版按用户提供的BIThesis 3.7.4样板重写，使用研究生`type=master`，封面为“专业学位硕士学位论文开题报告”，正文对应学校七栏目。题目：披露时点感知的A股短期尾部风险预测；姓名邱琨原；2年制。

原样板的本科正文、图像资源库设计、旧个人信息、已签署声明、致谢、参考文献和商业字体均不沿用。19幅图由本工程绘制，其中数据分析使用已验聚合，年龄包络为注明参数的解析示意，不展示虚构主模型性能。

## 结构

- `main.tex`：封面、目录、学校七栏、参考文献及正文页数标记。
- `sections/`：七栏目正文。
- `methods/`：研究方案的数据/事件、图/融合、实验/工程三个部分。
- `figures/figures.tex`：原创TikZ/PGFPlots矢量图，可直接编辑。
- `data/audit-summary.json`：已核聚合数字，不含公告、股东、标签或预测原行。
- 构建时生成`data/yearly.dat`、`data/missing.dat`及`references.bbl`。
- 源码包附用户样板中的**未修改、docstrip生成**的`bithesis.cls`依赖及许可说明，不包含原论文内容。

## 字体与编译

使用XeLaTeX，正文小四（12bp）、22bp行距；保留研究生模板的A4版心，不通过放大字号或空白页达到页数。中文优先Noto Serif/Sans CJK SC，环境无该字体时使用TeX Live的Fandol；英文使用TeX Gyre Termes。不分发原样板的STXIHEI.TTF。

Debian/Ubuntu所需软件为`texlive-xetex texlive-lang-chinese fonts-texgyre`。源码包内已包含生成的引文与聚合绘图表，可直接三次编译，无需使用原本科的Biber数据库：

```bash
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex
```

仓库原始源稿通过构建程序处理，原样板ZIP需留在私有位置：

```bash
python3 scripts/build_long_proposal.py \
  --template-zip /private/path/论文Latex.zip \
  --build-dir /private/path/new-report-build --publish
```

已有构建目录用`--use-prepared`重新编译；已有PDF/源码包只有审阅后才用`--overwrite`。程序要求原包SHA与实际接收记录一致，并检查末轮日志中的`REPORT_BODY_PAGES`。**正文至少30页，封面、目录、参考文献不计入这30页**；不够时构建失败，需补充实质内容，不改大行距凑页。

## 学术与提交边界

图中公司/机构字母仅为算法示意；年度记录是全量源统计，不等于训练数据；字段缺失不是机构图错误率。理论包络图取明确的示意参数，未估计任何训练模型。真实主模型尚未正式训练，人工金标0、主图未认证；研究基础中的先导分数只属于旧表格实验。

引用由维护的独立综述34条题录生成，列入引用不等于本人已完成30篇阅读。学号、导师、类别/领域、学院、年级和正式开题日期待填写。该工程是按所提供社区模板制作的开题稿，正式格式和手续仍需培养单位、导师确认。

公开源码包仅含自写报告/图表、非敏感统计、生成引文和模板类依赖，不含原用户论文、授权原始记录、模型或签署文件。模板许可及来源见`THIRD_PARTY_NOTICES.md`。

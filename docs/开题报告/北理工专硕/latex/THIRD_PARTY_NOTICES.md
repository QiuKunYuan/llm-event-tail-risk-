# 模板依赖与第三方许可

## BIThesis 3.7.4

本工程使用用户提供样板内的`bithesis.cls`，文件头标明其由`bithesis.dtx`经docstrip生成，版本为2024-04-16 / 3.7.4。

- 原著作权：2023，Association of Bit Network Pioneer及文档所列作者。
- 模板维护者：Feng Kaiyu。
- 许可：LaTeX Project Public License 1.3c或更新版本。
- 上游源码与说明：https://github.com/BITNP/BIThesis
- 许可正文：https://www.latex-project.org/lppl/lppl-1-3c.txt

随源码包分发的类文件保持原字节内容和原始版权/维护声明，没有把该依赖改名为本项目原创。模板源代码仍可从上游取得。本项目修改的是自写的`main.tex`、章节、图表及数据组织；仅利用模板的研究生排版功能。

用户原始压缩包SHA256为`21ab1e018f9fcb9bf522d9d508e88b4d65697e688980ca31dfb94e8fa6cd51e6`，原包在私有目录保留。本公开源码包不包含原本科章节、图片、签署文件、旧个人信息或字体TTF；只选择模板的生成类依赖。

## 字体与图表

正文使用系统/TeX发行版提供的Noto CJK或Fandol和TeX Gyre字体，不重新分发用户样板的商业字体文件；hyperref所需Zapf Dingbats指标由TeX发行版提供，也不随源码包分发。报告22幅图由本工程TikZ/PGFPlots绘制；两幅源统计图仅用真实历史聚合，年龄权重和旧logit帽两幅图为注明条件的数学分析，其他图为研究设计。未复制原论文图像、真实公告文本或授权逐样本记录。

构建目录和净源码包采用精确文件白名单。额外源行JSON、图片、字体、旧章节或签署文件即使存在于私有构建目录，也不会进入包；原类字节与收到的ZIP不一致时拒绝分发。自写开题内容采用实际损失与四档概率混合，不是原本科正文或旧残差报告的重新包装。
